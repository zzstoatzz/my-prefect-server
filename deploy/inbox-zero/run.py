import json
import os
from pathlib import Path

from mps.inbox.server import main

config = Path(os.environ.get("INBOX_CONFIG", str(Path.home() / ".config/inbox-zero/config.json")))
for key, value in json.loads(config.read_text()).items():
    if key.startswith("INBOX_"):
        os.environ[key] = value
main()
