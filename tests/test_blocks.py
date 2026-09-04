import json

import pytest
from mps.blocks import _mapping, _text


def test_mapping_unwraps_the_ui_json_kind_wrapper():
    creds = {"handle": "h", "password": "p", "pds": "https://pds"}
    wrapped = {"value": json.dumps(creds), "__prefect_kind": "json"}
    assert _mapping("operator-atproto-creds", wrapped) == creds


def test_mapping_accepts_a_dict_or_json_text():
    assert _mapping("x", {"a": 1}) == {"a": 1}
    assert _mapping("x", '{"a": 1}') == {"a": 1}
    assert _mapping("x", {"value": {"a": 1}}) == {"a": 1}


def test_mapping_rejects_non_objects():
    with pytest.raises(TypeError):
        _mapping("x", "just a string")
    with pytest.raises(TypeError):
        _mapping("x", 42)


def test_text_requires_str():
    assert _text("x", "s") == "s"
    with pytest.raises(TypeError):
        _text("x", {"value": "s"})
