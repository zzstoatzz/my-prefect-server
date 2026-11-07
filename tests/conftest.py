import pytest

# git exports these to hooks and `rebase --exec`; inherited by a fixture's `git -C tmp`,
# they redirect it at this checkout (`init --bare` flipped core.bare on 2026-11-07).
GIT_LOCATION_VARIABLES = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_WORK_TREE",
)


@pytest.fixture(autouse=True)
def _isolate_git(monkeypatch):
    for name in GIT_LOCATION_VARIABLES:
        monkeypatch.delenv(name, raising=False)
