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
