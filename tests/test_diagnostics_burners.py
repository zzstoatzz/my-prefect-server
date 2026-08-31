"""sustained-burner classification in the diagnostics flow.

regression for the simulator incident: a load generator ran two days at
100% cpu on heavypad before anyone noticed. these pin what flags and,
just as importantly, what must never flag (the prefect workload itself).
"""

from flows.diagnostics import parse_ps, sustained_burners

PS_OUTPUT = """\
 458333  180000  175500  0.4 /home/stoat/.cache/go-build/40/…/simulator serve --reset --accounts=100
   2697  260000    1200  0.2 /home/stoat/.cache/uv/…/bin/prefect worker start --pool home-pool
  90001    7200    7100  1.0 uv run --with 'my-prefect-server @ git+…' prefect flow-run execute
    910  260000  120000  0.0 [irq/213-iwlwifi:queue_13]
   3437  260000   21700  0.2 /usr/local/bin/spindle
  99999     600     590  0.1 cargo build --release
"""


def procs():
    return parse_ps(PS_OUTPUT)


def test_parse_ps():
    parsed = procs()
    assert len(parsed) == 6
    sim = parsed[0]
    assert sim["pid"] == 458333
    assert sim["cpu_s"] == 175500
    assert "simulator serve" in sim["args"]


def test_runaway_simulator_flags():
    flagged = sustained_burners(procs())
    assert [p["pid"] for p in flagged] == [458333]


def test_prefect_workload_never_flags():
    flagged = sustained_burners(procs())
    assert not any("prefect" in p["args"] for p in flagged)


def test_kernel_threads_never_flag():
    flagged = sustained_burners(procs())
    assert not any(p["args"].startswith("[") for p in flagged)


def test_young_hot_process_does_not_flag():
    # a build pegging a core for 10 minutes is a busy afternoon, not a leak
    assert not any(p["pid"] == 99999 for p in sustained_burners(procs()))


def test_old_mostly_idle_daemon_does_not_flag():
    # spindle: old and nonzero cpu, but duty cycle far below the bar
    assert not any(p["pid"] == 3437 for p in sustained_burners(procs()))
