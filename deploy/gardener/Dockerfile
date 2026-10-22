FROM golang:1.26.0-alpine@sha256:d4c4845f5d60c6a974c6000ce58ae079328d03ab7f721a0734277e69905473e5 AS tsunplug
ENV CGO_ENABLED=0 GOFLAGS=-trimpath
RUN go install github.com/tailscale/ts-plug/cmd/ts-unplug@0b0b083dae1aad0ad55c62ba28d3402f421a9aab

FROM ubuntu:24.04 AS base
# eMudhra distrust; see Dockerfile.trust for how the roots were matched
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates curl git python3 sudo systemd systemd-sysv dbus bubblewrap socat ripgrep fd-find \
    && apt-get clean \
    && sed -i -e 's|^mozilla/emSign_ECC_Root_CA_-_G3.crt|!&|' \
              -e 's|^mozilla/Comodo_AAA_Services_root.crt|!&|' /etc/ca-certificates.conf \
    && update-ca-certificates --fresh
RUN ln -s /usr/bin/fdfind /usr/bin/fd
RUN useradd --create-home --shell /bin/bash exedev \
    && printf 'exedev ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/exedev \
    && chmod 0440 /etc/sudoers.d/exedev

FROM base AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/gardener/venv UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /build
RUN uv python install 3.13.7
COPY pyproject.toml uv.lock ./
COPY packages/mps/pyproject.toml packages/mps/pyproject.toml
COPY packages/prefect-exe/pyproject.toml packages/prefect-exe/pyproject.toml
COPY packages/prefect-sprites/pyproject.toml packages/prefect-sprites/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package mps --python 3.13.7 --no-dev --no-install-workspace
COPY packages/mps/src packages/mps/src
COPY flows flows
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package mps --python 3.13.7 --no-dev --no-editable
RUN /opt/gardener/venv/bin/python -c 'from mps.pi_execution import prepare_pi_runtime; prepare_pi_runtime()'

FROM base AS runtime
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
LABEL exe.dev/login-user=exedev
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=builder /opt/python /opt/python
COPY --from=builder /opt/gardener /opt/gardener
COPY --from=builder /opt/phi-agent /opt/phi-agent
COPY --from=tsunplug /go/bin/ts-unplug /usr/local/bin/ts-unplug
RUN ln -sf /etc/ssl/certs/ca-certificates.crt \
    "$(/opt/gardener/venv/bin/python -c 'import certifi; print(certifi.where())')"
ENV PATH="/opt/gardener/venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN python -c 'import flows.pi_agent, flows.autofix_revise, flows.test_pull_patch; from mps.pi_execution import prepare_pi_runtime; prepare_pi_runtime()'
WORKDIR /home/exedev
ENTRYPOINT ["/sbin/init"]
