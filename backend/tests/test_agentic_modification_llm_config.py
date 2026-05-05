import types
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fake_aiohttp = types.ModuleType("aiohttp")
fake_aiohttp.ClientError = Exception
fake_aiohttp.ClientTimeout = lambda total: {"total": total}
fake_aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", fake_aiohttp)

from app.service.rag.agentic_modification.services import llm_client


def test_modification_agent_prefers_canonical_llm_envs(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("MOD_AGENT_LLM_URL", "https://legacy-mod-agent.example/v1/chat/completions")
    monkeypatch.setenv("MOD_AGENT_LLM_KEY", "legacy-mod-agent-key")
    monkeypatch.setenv("MOD_AGENT_LLM_MODEL", "legacy-mod-agent-model")

    url, api_key, model = llm_client._resolve_runtime_config()

    assert url == "https://api.deepseek.com/chat/completions"
    assert api_key == "canonical-key"
    assert model == "deepseek-v4-flash"
