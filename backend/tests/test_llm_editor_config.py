import importlib.util
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fake_aiohttp = types.ModuleType("aiohttp")
fake_aiohttp.ClientError = Exception
fake_aiohttp.ClientTimeout = lambda total: {"total": total}
fake_aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", fake_aiohttp)

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "service"
    / "modification"
    / "llm_editor_service.py"
)
_SPEC = importlib.util.spec_from_file_location("llm_editor_service_test_module", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


def test_llm_editor_prefers_canonical_llm_envs(monkeypatch):
    monkeypatch.setenv("LLM_EDITOR_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("LLM_API_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "deepseek-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter-model")
    monkeypatch.setenv("LLM_EDITOR_OPENROUTER_MODEL", "editor-model")

    cfg = _MODULE._load_config()

    assert cfg.provider == "OPENROUTER"
    assert cfg.openrouter_url == "https://api.deepseek.com/chat/completions"
    assert cfg.openrouter_api_key == "deepseek-key"
    assert cfg.openrouter_model == "deepseek-v4-flash"
