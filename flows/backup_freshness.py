"""Fail when HeavyPad's local, R2 or restore-verified backups go stale.

A failed run is the alert: HeavyPad's existing flow-run failure rule notifies.
"""

import datetime as dt
import json
import re
from pathlib import Path

from prefect import flow

STATE_DIR = "/home/stoat/.local/state/prefect-home"
DUMP = re.compile(r"^(\d{8}T\d{6}Z)\.dump\.age$")
MAX_BACKUP_AGE = dt.timedelta(hours=26)
MAX_RESTORE_AGE = dt.timedelta(days=8)


def dump_time(name: str) -> dt.datetime:
    match = DUMP.match(name)
    if match is None:
        raise ValueError(f"not a dump name: {name}")
    return dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)


def problems(
    now: dt.datetime,
    local_names: list[str],
    offsite: dict | None,
    restore: dict | None,
) -> list[str]:
    found = []
    dumps = sorted(name for name in local_names if DUMP.match(name))
    newest = dumps[-1] if dumps else None
    if newest is None:
        found.append("no local dump")
    elif now - dump_time(newest) > MAX_BACKUP_AGE:
        found.append(f"newest local dump {newest} is older than {MAX_BACKUP_AGE}")
    if offsite is None:
        found.append("no R2 upload has been recorded")
    else:
        if newest is not None and offsite.get("newest_uploaded") != newest:
            found.append(f"R2 newest is {offsite.get('newest_uploaded')}, local newest is {newest}")
        checked = dt.datetime.fromisoformat(offsite["checked_at"])
        if now - checked > MAX_BACKUP_AGE:
            found.append(f"R2 upload last succeeded at {offsite['checked_at']}")
    if restore is None:
        found.append("no restore from R2 has been verified")
    elif now - dt.datetime.fromisoformat(restore["verified_at"]) > MAX_RESTORE_AGE:
        found.append(f"last verified R2 restore was at {restore['verified_at']}")
    return found


def read_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


@flow(name="backup-freshness", log_prints=True, timeout_seconds=60)
def backup_freshness(state_dir: str = STATE_DIR) -> list[str]:
    state = Path(state_dir)
    found = problems(
        dt.datetime.now(dt.UTC),
        [path.name for path in (state / "backups").iterdir()],
        read_json(state / "offsite.json"),
        read_json(state / "restore-verified.json"),
    )
    if found:
        raise RuntimeError("; ".join(found))
    print("Local, R2 and restore-verified backups are fresh")
    return found


if __name__ == "__main__":
    backup_freshness()
