"""Worker-side exe.dev authentication."""

import os
from pathlib import Path

from prefect.blocks.core import Block
from pydantic import Field


class ExeCredentials(Block):
    """The SSH identity registered with the exe.dev account hosting flow runs."""

    _block_type_name = "exe.dev Credentials"
    identity_file: Path | None = Field(
        default_factory=lambda: (
            Path(value) if (value := os.environ.get("EXE_IDENTITY_FILE")) else None
        ),
        description=(
            "Private key path on the worker host; never sent to a VM. "
            "Unset uses the worker user's default SSH identity."
        ),
    )
