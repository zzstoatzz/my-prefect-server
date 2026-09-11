"""Per-attempt Unix-socket access to one trusted inference endpoint."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from mps.inference_models import resolve_inference_model

if TYPE_CHECKING:
    from mps.inference_grants import InferenceGrants


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BridgeServer(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True


@contextmanager
def inference_bridge(
    socket_path: Path | None,
    *,
    upstream: str,
    model: str = "openai/gpt-5.6-luna",
    authorization: str | None = None,
    anthropic_api_key: str | None = None,
    translate_model: bool = True,
    max_requests: int = 32,
    max_output_tokens: int = 8192,
    agent_uid: int | None = 2000,
    listen: tuple[str, int] | None = None,
    grants: InferenceGrants | None = None,
    workflow_requests=None,
):
    """Expose authorized native inference APIs; do not forward caller headers or redirects.

    The socket's parent must be a trusted per-attempt directory. The caller owns
    upstream identity and budget configuration, never the sandboxed process.
    """
    if max_requests < 1 or max_output_tokens < 1:
        raise ValueError("Inference limits must be positive")
    if (socket_path is None) == (listen is None):
        raise ValueError("Choose one listener")
    if listen is not None and grants is None:
        raise ValueError("TCP inference requires durable run grants")
    if workflow_requests is not None and listen is None:
        raise ValueError("Workflow requests cannot be exposed inside an agent namespace")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    lock = threading.Lock()
    counters = {"requests": 0, "succeeded": 0}

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(60)

        def log_message(self, format, *args):
            pass  # Never log prompts, upstream errors, or credentials.

        def do_POST(self):
            if self.path not in ("/v1/chat/completions", "/v1/messages"):
                return self.handle_post()
            started = time.monotonic()
            self.audit_status = None
            self.audit_outcome = "failed"
            identity = {}
            credential = self.headers.get("Authorization", "")
            if grants is not None and credential.startswith("Bearer "):
                identity = grants.identify(credential.removeprefix("Bearer "))
            try:
                self.handle_post()
            finally:
                logging.getLogger(__name__).info(
                    "inference_request %s",
                    json.dumps(
                        {
                            "request_id": str(uuid4()),
                            "attempt": identity.get("attempt"),
                            "model": identity.get("model", model),
                            "status": self.audit_status,
                            "outcome": self.audit_outcome,
                            "duration_ms": round((time.monotonic() - started) * 1000),
                        }
                    ),
                )

        def send_response(self, code, message=None):
            self.audit_status = code
            if 400 <= code < 500:
                self.audit_outcome = "denied"
            return super().send_response(code, message)

        def handle_post(self):
            if self.path == "/workflows/request" and workflow_requests is not None:
                if not workflow_requests.authorized(self.headers.get("Authorization", "")):
                    self.send_error(401)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= 65536:
                        raise ValueError("Invalid request size")
                    result = workflow_requests.request(json.loads(self.rfile.read(length)))
                except (ValueError, TypeError):
                    self.send_error(400, "Invalid workflow request")
                    return
                except Exception:
                    self.send_error(
                        502, "Workflow request was not confirmed; retry with the same request key"
                    )
                    return
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if grants is not None and not self.headers.get("Authorization", "").startswith(
                "Bearer "
            ):
                self.send_error(401)
                return
            if self.path not in ("/v1/chat/completions", "/v1/messages"):
                self.send_error(404)
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Chunked requests are unsupported")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or not isinstance(body.get("model"), str):
                    raise ValueError("Model is required")
                selected = resolve_inference_model(body["model"])
                if (grants is None and selected.name != model) or self.path != selected.path:
                    raise ValueError("Unauthorized model or API")
                if not isinstance(body.get("messages"), list):
                    raise ValueError("Messages are required")
                for key in ("max_tokens", "max_completion_tokens"):
                    if key in body:
                        value = body[key]
                        if type(value) is not int or value < 1:
                            raise ValueError("Invalid output limit")
                        body[key] = min(value, max_output_tokens, selected.max_output_tokens)
                if "max_tokens" not in body and "max_completion_tokens" not in body:
                    limit_key = (
                        "max_tokens"
                        if selected.api == "anthropic-messages"
                        else "max_completion_tokens"
                    )
                    body[limit_key] = min(max_output_tokens, selected.max_output_tokens)
            except (ValueError, TypeError):
                self.send_error(400, "Invalid inference request")
                return
            if grants is not None and not grants.consume(token, selected.name):
                self.send_error(403, "Inference grant unavailable")
                return
            with lock:
                if grants is None and counters["requests"] >= max_requests:
                    self.send_error(429, "Attempt inference limit reached")
                    return
                counters["requests"] += 1
            headers = {"Content-Type": "application/json"}
            if authorization:
                headers["Authorization"] = authorization
            endpoint = upstream
            if selected.api == "anthropic-messages":
                endpoint = upstream.removesuffix("/v1/chat/completions").rstrip("/") + selected.path
                headers["anthropic-version"] = "2023-06-01"
                if anthropic_api_key:
                    headers["x-api-key"] = anthropic_api_key
            body["model"] = selected.wire_name if translate_model else selected.name
            request = Request(endpoint, data=json.dumps(body).encode(), headers=headers)
            try:
                response = opener.open(request, timeout=60)
            except HTTPError as exc:
                exc.close()
                self.send_error(502, "Inference endpoint rejected the request")
                return
            except (URLError, TimeoutError):
                self.send_error(502, "Inference endpoint unavailable")
                return
            with response:
                self.send_response(response.status)
                self.send_header(
                    "Content-Type", response.headers.get("Content-Type", "application/json")
                )
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    self.audit_outcome = "interrupted"
                    return
            with lock:
                counters["succeeded"] += 1
            self.audit_outcome = "succeeded"

    server = (
        ThreadingHTTPServer(listen, Handler)
        if listen is not None
        else BridgeServer(str(socket_path), Handler)
    )
    try:
        if socket_path is not None:
            if agent_uid is not None:
                os.chown(socket_path, agent_uid, agent_uid)
            os.chmod(socket_path, 0o600)
        else:
            counters["port"] = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield counters
        finally:
            server.shutdown()
            thread.join(timeout=5)
    finally:
        server.server_close()
        if socket_path is not None:
            socket_path.unlink(missing_ok=True)
