"""Worker-side Sprites authentication."""

import os

from prefect.blocks.core import Block
from pydantic import Field, SecretStr


class SpritesCredentials(Block):
    """An API token for the Sprites organization hosting flow runs."""

    _block_type_name = "Sprites Credentials"
    token: SecretStr = Field(
        default_factory=lambda: SecretStr(os.environ.get("SPRITE_TOKEN", "")),
        description="Sprites API token. Kept on the worker; never sent to a Sprite.",
    )

    def get_token(self) -> str:
        value = self.token.get_secret_value()
        if not value:
            raise ValueError("Configure Sprites Credentials or set SPRITE_TOKEN on the worker")
        return value
