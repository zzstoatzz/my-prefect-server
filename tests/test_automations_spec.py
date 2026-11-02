import re
from pathlib import Path

import yaml

SPEC = Path(__file__).parent.parent / "deploy" / "automations.yaml"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def test_automations_name_their_blocks_and_deployments_instead_of_server_uuids():
    automations = yaml.safe_load(SPEC.read_text())["automations"]
    assert automations
    for item in automations:
        assert not UUID.search(repr(item)), item["name"]
        for action in item["actions"]:
            assert "block_document_id" not in action and "deployment_id" not in action
            if action["type"] == "send-notification":
                assert action["block"], item["name"]
            if action["type"] == "run-deployment":
                assert action["deployment"], item["name"]
