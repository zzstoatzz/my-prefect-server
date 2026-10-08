#!/usr/bin/env bash
set -euo pipefail
wheel=$(realpath "$1")
expected=$2
base_image=$3
image=$4
test "$(shasum -a 256 "$wheel" | cut -d ' ' -f 1)" = "$expected"
mkdir -p "$5"
context=$(realpath "$5")
cp "$wheel" "$context/mps-0.1.0-py3-none-any.whl"
cp deploy/gardener/verify-wheel.py "$context/verify-wheel.py"
cat > "$context/pyproject.toml" <<'TOML'
[project]
name = "gardener-release"
version = "0.1.0"
requires-python = "==3.13.*"
dependencies = ["mps", "prefect==3.7.7"]

[tool.uv.sources]
mps = { path = "mps-0.1.0-py3-none-any.whl" }
TOML
uv lock --project "$context" --python 3.13.7
docker build --platform linux/amd64 -f deploy/gardener/Dockerfile.wheel \
    --build-arg BASE_IMAGE="$base_image" --build-arg WHEEL_SHA256="$expected" \
    -t "$image" "$context"
