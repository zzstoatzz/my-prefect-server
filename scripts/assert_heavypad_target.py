"""Reject accidental registration against the original server or a Cloud profile."""

from prefect.client.orchestration import get_client
from prefect.settings import PREFECT_API_URL

TARGET = "http://100.96.216.23:14200/api"


def main():
    assert (PREFECT_API_URL.value() or "").rstrip("/") == TARGET, (
        "Registration must target HeavyPad"
    )
    with get_client(sync_client=True) as client:
        pool = client.read_work_pool("heavypad-pool")
        assert pool.type == "process"
    print("Confirmed HeavyPad registration target and existing process pool")


if __name__ == "__main__":
    main()
