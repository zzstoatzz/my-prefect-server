"""The private recovery signal stored in Prefect's event log."""

from pydantic import AwareDatetime, BaseModel, Field

EVENT_NAME = "incident.recovery_failed"
RESOURCE_ID = "fleet-recovery.cellar"


class RecoveryFailed(BaseModel):
    machine: str = Field(pattern=r"^[a-z0-9]+$", max_length=40)
    release: str | None = Field(default=None, max_length=128)
    attempts: int = Field(gt=0)
    exit_code: int | None = None
    oom_killed: bool
    failed_at: AwareDatetime
    sweep_url: str | None = Field(default=None, max_length=256)
