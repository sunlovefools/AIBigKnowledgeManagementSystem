from pathlib import Path


AUTH_SERVICE_PATH = Path("backend/app/service/auth/auth_service.py")
ROUTER_AUTH_PATH = Path("backend/app/api/router_auth.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_auth_service_is_auth0_only():
    content = _read(AUTH_SERVICE_PATH)
    assert "def register_user(" not in content
    assert "def login_user(" not in content
    assert "def auth0_login(" in content
    assert "def oauth_login(" in content


def test_router_exposes_only_auth0_login_route():
    content = _read(ROUTER_AUTH_PATH)
    assert "\"/auth0-login\"" in content
    assert "\"/register\"" not in content
    assert "\"/login\"" not in content
