"""Schedule one-shot Hue fades without replaying writes."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

HOME_TIMEZONE = ZoneInfo("America/Chicago")
MAX_START_DELAY = timedelta(minutes=5)


class WindDown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: int = Field(default=20, ge=15, le=30)
    brightness: float = Field(default=5, ge=1, le=100)
    override: bool = False


def override_name(day: date) -> str:
    return f"lighting-wind-down-{day.isoformat()}"
