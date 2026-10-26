import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "heavypad_prefect_secrets",
    Path(__file__).resolve().parent.parent / "scripts/heavypad_prefect_secrets.py",
)
secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secrets)

CREDENTIALS = {
    "r2_backup_access_key_id": "key-id",
    "r2_backup_secret_access_key": "secret",
    "r2_backup_endpoint": "https://account.r2.cloudflarestorage.com",
    "r2_backup_bucket": "heavypad-prefect-backups",
}


def test_r2_environment_absent_until_store_has_keys():
    assert secrets.r2_environment({"api_auth_string": "x"}) is None


def test_r2_environment_renders_rclone_remote_and_bucket():
    lines = secrets.r2_environment(CREDENTIALS).splitlines()
    assert "RCLONE_CONFIG_R2_PROVIDER=Cloudflare" in lines
    assert "RCLONE_CONFIG_R2_ENDPOINT=https://account.r2.cloudflarestorage.com" in lines
    assert "RCLONE_CONFIG_R2_ACCESS_KEY_ID=key-id" in lines
    assert "RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=secret" in lines
    assert "PREFECT_HOME_R2_BUCKET=heavypad-prefect-backups" in lines
