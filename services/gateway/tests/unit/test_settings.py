import pytest
from pydantic import ValidationError

from wmg_gateway.settings import Settings


def test_valid_settings(base_env: dict[str, str]) -> None:
    s = Settings(database_url="postgresql://x", **base_env)  # type: ignore[arg-type]
    assert len(s.encryption_key()) == 32
    assert "dashboard.localhost" in s.host_list()


@pytest.mark.parametrize("key", ["not base64!!", "YWJj"])
def test_encryption_key_must_be_32_bytes(base_env: dict[str, str], key: str) -> None:
    with pytest.raises(ValidationError, match="credential_encryption_key"):
        Settings(database_url="postgresql://x", **(base_env | {"credential_encryption_key": key}))  # type: ignore[arg-type]
