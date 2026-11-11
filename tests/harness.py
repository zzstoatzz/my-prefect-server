"""Run flows against a temporary Prefect server that is given time to start."""

from contextlib import contextmanager

from prefect.testing.utilities import prefect_test_harness as _prefect_test_harness

# Each harness boots its own API server. Eighteen xdist workers doing that on a
# busy machine outran the upstream thirty-second default, which failed tests
# that have nothing to do with startup time. This only bounds a server that
# never comes up.
SERVER_STARTUP_TIMEOUT_SECONDS = 300


@contextmanager
def prefect_test_harness():
    with _prefect_test_harness(server_startup_timeout=SERVER_STARTUP_TIMEOUT_SECONDS):
        yield
