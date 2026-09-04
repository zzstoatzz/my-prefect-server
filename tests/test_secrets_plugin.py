import importlib.metadata

import pytest
from mps import secrets_plugin


class FakeSecret:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


@pytest.fixture
def fake_blocks(monkeypatch):
    blocks = {"turso-url-x": "libsql://example.turso.io", "hetzner-tokens-x": {"a": "1"}}

    async def aload(name):
        return FakeSecret(blocks[name])

    monkeypatch.setattr("prefect.blocks.system.Secret.aload", staticmethod(aload))
    return blocks


async def test_resolves_sentinel_env(monkeypatch, fake_blocks):
    monkeypatch.setenv("TURSO_URL", "prefect-block://turso-url-x")
    monkeypatch.setenv("PLAIN", "untouched")
    result = await secrets_plugin.setup_environment(ctx=None)
    assert result.env == {"TURSO_URL": "libsql://example.turso.io"}
    assert "resolved 1" in result.note


async def test_non_string_block_value_becomes_json(monkeypatch, fake_blocks):
    monkeypatch.setenv("HETZNER", "prefect-block://hetzner-tokens-x")
    result = await secrets_plugin.setup_environment(ctx=None)
    assert result.env == {"HETZNER": '{"a": "1"}'}


async def test_no_sentinels_returns_none(monkeypatch):
    for k, v in list(__import__("os").environ.items()):
        if v.startswith(secrets_plugin.SCHEME):
            monkeypatch.delenv(k)
    assert await secrets_plugin.setup_environment(ctx=None) is None


def test_entry_point_registered():
    eps = importlib.metadata.entry_points(group="prefect.plugins")
    ours = {ep.name: ep.value for ep in eps}
    assert ours.get("mps-secrets") == "mps.secrets_plugin"
