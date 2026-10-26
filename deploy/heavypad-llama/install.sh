#!/usr/bin/env bash
# Install gemma-4-12b on llama.cpp's CUDA build as a systemd user service on
# heavypad, so the OpenAI-compatible endpoint on 127.0.0.1:18080 survives
# reboots. No root needed: the stoat user has lingering enabled.
# Run on heavypad:  bash install.sh
#
# The flags are the ones the coral entity-quality evals were measured with.
# All layers fit the 8GB 4070 only with q8_0 KV cache and flash attention;
# f16 KV fails to allocate. The binary carries an rpath to the user-local
# CUDA 12.8 toolkit, so no LD_LIBRARY_PATH is needed.
set -euo pipefail

BIN="$HOME/.local/opt/llama.cpp/gpu-build/src-b10639/build-cuda/bin/llama-server"
MODEL="$HOME/.cache/llama.cpp/models/gemma-4-12b-it-Q4_K_M.gguf"
UNIT_DIR="$HOME/.config/systemd/user"

test -x "$BIN" || { echo "missing $BIN" >&2; exit 1; }
test -f "$MODEL" || { echo "missing $MODEL" >&2; exit 1; }
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/heavypad-llama.service" <<UNIT
[Unit]
Description=gemma-4-12b on llama.cpp CUDA, OpenAI-compatible on 127.0.0.1:18080

[Service]
ExecStart=${BIN} -m ${MODEL} --host 127.0.0.1 --port 18080 -ngl 99 -fa on -ctk q8_0 -ctv q8_0 -c 8192 --parallel 2 --jinja
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable heavypad-llama.service

if ss -tln | grep -q '127.0.0.1:18080 '; then
  echo "==> enabled; :18080 is held by another process, so not starting now"
  exit 0
fi
systemctl --user start heavypad-llama.service
for _ in $(seq 60); do
  curl -sf --max-time 3 http://127.0.0.1:18080/health >/dev/null && break
  sleep 2
done
curl -sf --max-time 3 http://127.0.0.1:18080/health >/dev/null \
  || { echo "llama-server not healthy on :18080" >&2; exit 1; }
echo "==> ok: heavypad-llama serving on 127.0.0.1:18080"
