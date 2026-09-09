"""Linux execution boundary for Pi on the Phi Sprite.

The caller supplies a scratch checkout and a dedicated agent home. This launcher
never exposes the host root, Sprite management socket, or orchestration files.
"""

from pathlib import Path

AGENT_UID = 2000
PI_ENTRYPOINT = "/opt/phi-agent/pi/node_modules/@earendil-works/pi-coding-agent/dist/cli.js"


def aperture_models(base_url: str = "http://127.0.0.1:8888/v1") -> dict:
    """Pi's public models.json contract, using the local inference bridge."""
    return {
        "providers": {
            "aperture": {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": "local-inference-bridge",
                "models": [{"id": "openai/gpt-5.6-luna", "maxTokens": 8192}],
            }
        }
    }


def sandbox_command(
    *,
    workspace: Path,
    home: Path,
    tools: Path,
    command: list[str],
    inference_socket: Path | None = None,
) -> list[str]:
    """Build the root-invoked launcher; the final command runs without privilege.

    `tools` must be a trusted, credential-free installation directory. The
    writable paths must be dedicated to this attempt and owned by AGENT_UID.
    Network access is isolated; the optional socket is the sole inference route.
    """
    if not command:
        raise ValueError("An agent command is required")
    for path in (workspace, home, tools):
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            raise ValueError("Sandbox mounts must be absolute, real directories")
    args = [
        "bwrap",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--perms",
        "0755",
        "--dir",
        "/home",
        "--perms",
        "0755",
        "--dir",
        "/opt",
        "--perms",
        "0755",
        "--dir",
        "/run",
        "--ro-bind",
        str(tools),
        "/opt/phi-agent",
        "--bind",
        str(workspace),
        "/workspace",
        "--bind",
        str(home),
        "/home/agent",
        "--clearenv",
        "--setenv",
        "PATH",
        "/opt/phi-agent/node/bin:/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/home/agent",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "PI_CODING_AGENT_DIR",
        "/home/agent/.pi/agent",
        "--chdir",
        "/workspace",
    ]
    if inference_socket is not None:
        if not inference_socket.is_absolute() or not inference_socket.is_socket():
            raise ValueError("Inference endpoint must be an existing Unix socket")
        args += ["--ro-bind", str(inference_socket), "/run/aperture.sock"]
    if (workspace / ".git").is_dir():
        # Pi edits working files; publishing and Git configuration stay trusted.
        args += ["--ro-bind", str(workspace / ".git"), "/workspace/.git"]
    return [
        *args,
        "--",
        "/usr/bin/setpriv",
        "--reuid",
        str(AGENT_UID),
        "--regid",
        str(AGENT_UID),
        "--clear-groups",
        "--no-new-privs",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        *command,
    ]
