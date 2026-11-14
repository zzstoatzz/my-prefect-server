"""The R2 backup bucket that holds durable billing observations."""

from __future__ import annotations

from pathlib import Path

import boto3
from dotenv import dotenv_values


def archive_client():
    config = dotenv_values(Path.home() / ".config/prefect-home/r2.env")
    return boto3.client(
        "s3",
        endpoint_url=config["RCLONE_CONFIG_R2_ENDPOINT"],
        aws_access_key_id=config["RCLONE_CONFIG_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=config["RCLONE_CONFIG_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    ), config["PREFECT_HOME_R2_BUCKET"]
