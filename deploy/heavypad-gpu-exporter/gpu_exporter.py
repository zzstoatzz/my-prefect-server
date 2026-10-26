"""Expose nvidia-smi readings as Prometheus metrics, standard library only.

Runs on heavypad as a systemd user service (see install.sh) and binds only to
the tailnet address, like the node exporter beside it.
"""

import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = os.environ.get("GPU_EXPORTER_LISTEN", "100.96.216.23:9835")

FIELDS = [
    ("temperature.gpu", "nvidia_gpu_temperature_celsius", "GPU core temperature."),
    ("utilization.gpu", "nvidia_gpu_utilization_ratio", "Fraction of time a kernel ran."),
    ("utilization.memory", "nvidia_gpu_memory_utilization_ratio", "Fraction of time memory was read or written."),
    ("memory.used", "nvidia_gpu_memory_used_bytes", "VRAM in use."),
    ("memory.total", "nvidia_gpu_memory_total_bytes", "VRAM installed."),
    ("power.draw", "nvidia_gpu_power_draw_watts", "Board power draw."),
    ("enforced.power.limit", "nvidia_gpu_power_limit_watts", "Enforced power limit."),
    ("clocks.sm", "nvidia_gpu_sm_clock_hertz", "SM clock."),
    ("clocks.max.sm", "nvidia_gpu_sm_clock_max_hertz", "Maximum SM clock."),
    ("pstate", "nvidia_gpu_pstate", "Performance state, 0 is highest."),
    ("clocks_event_reasons.hw_thermal_slowdown", "nvidia_gpu_thermal_slowdown_hw", "1 when the hardware thermal slowdown is active."),
    ("clocks_event_reasons.sw_thermal_slowdown", "nvidia_gpu_thermal_slowdown_sw", "1 when the driver thermal slowdown is active."),
    ("clocks_event_reasons.sw_power_cap", "nvidia_gpu_power_cap_active", "1 when clocks are held down by the power cap."),
]
SCALE = {"utilization.gpu": 0.01, "utilization.memory": 0.01, "memory.used": 2**20, "memory.total": 2**20, "clocks.sm": 1e6, "clocks.max.sm": 1e6}


def parse(field, raw):
    raw = raw.strip()
    if field == "pstate":
        return float(raw.lstrip("P"))
    if field.startswith("clocks_event_reasons."):
        return 1.0 if raw == "Active" else 0.0
    return float(raw) * SCALE.get(field, 1)


def scrape():
    query = ",".join(["index", "name"] + [f for f, _, _ in FIELDS])
    out = subprocess.run(
        ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    samples = {name: [] for _, name, _ in FIELDS}
    for line in out.strip().splitlines():
        index, gpu_name, *values = [v.strip() for v in line.split(",")]
        labels = f'gpu="{index}",name="{gpu_name}"'
        for (field, name, _), raw in zip(FIELDS, values):
            try:
                samples[name].append(f"{name}{{{labels}}} {parse(field, raw)}")
            except ValueError:
                pass
    lines = []
    for _, name, help_text in FIELDS:
        if samples[name]:
            lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", *samples[name]]
    return lines


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        try:
            lines = scrape() + ["nvidia_smi_up 1"]
        except (subprocess.SubprocessError, OSError):
            lines = ["nvidia_smi_up 0"]
        body = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    host, port = LISTEN.rsplit(":", 1)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
