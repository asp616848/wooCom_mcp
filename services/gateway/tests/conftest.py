import base64

import pytest

PEPPER = "p" * 40
JWT = "j" * 40
KEY_B64 = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def base_env() -> dict[str, str]:
    """Valid settings except the database URL (tests add their own)."""
    return {
        "api_key_pepper": PEPPER,
        "credential_encryption_key": KEY_B64,
        "jwt_secret": JWT,
        "bootstrap_admin_password": "admin-password-123",
    }
