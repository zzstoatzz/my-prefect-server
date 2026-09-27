from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from time import monotonic
from typing import Any

import logfire


@dataclass
class TriageReport:
    run_id: str
    run_url: str
    publish_prs: bool
    status: str = "noop"
    candidates: int = 0
    triaged: int = 0
    skipped: int = 0
    failed: int = 0
    blocked: int = 0
    prs: int = 0
    cost_usd: float = 0
    usage: str = ""

    def thread(self, number: int, outcome: str, **attributes: Any) -> None:
        logfire.info(
            "fastmcp triage thread",
            run_id=self.run_id,
            run_url=self.run_url,
            publish_prs=self.publish_prs,
            number=number,
            outcome=outcome,
            thread_url=f"https://github.com/PrefectHQ/fastmcp/issues/{number}",
            **attributes,
        )


@contextmanager
def observe_triage(report: TriageReport) -> Iterator[TriageReport]:
    started = monotonic()
    logfire.info("fastmcp triage started", **asdict(report))
    error_type = ""
    try:
        yield report
    except BaseException as exc:
        report.status = "failed"
        error_type = type(exc).__name__
        raise
    finally:
        logfire.log(
            "error"
            if report.status == "failed"
            else "warn"
            if report.status == "degraded"
            else "info",
            "fastmcp triage finished",
            attributes={
                **asdict(report),
                "duration_seconds": monotonic() - started,
                "error_type": error_type,
            },
        )
        logfire.force_flush(timeout_millis=5000)
