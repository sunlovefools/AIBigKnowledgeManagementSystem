import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "service"
    / "rag"
    / "ingestion"
    / "docling"
    / "table_semantic"
    / "config.py"
)
_SPEC = importlib.util.spec_from_file_location("table_semantic_config_test_module", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_table_semantic_prefers_canonical_llm_envs(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("TABLE_SEMANTIC_LLM_URL", "https://legacy-table.example/v1/chat/completions")
    monkeypatch.setenv("TABLE_SEMANTIC_LLM_API_KEY", "legacy-table-key")
    monkeypatch.setenv("TABLE_SEMANTIC_CLASSIFIER_MODEL", "legacy-classifier")
    monkeypatch.setenv("TABLE_SEMANTIC_GLOBAL_MODEL", "legacy-global")
    monkeypatch.setenv("TABLE_SEMANTIC_ROW_MODEL", "legacy-row")

    assert _MODULE.get_table_semantic_llm_url() == "https://api.deepseek.com/chat/completions"
    assert _MODULE.get_table_semantic_llm_api_key() == "canonical-key"
    assert _MODULE.get_classifier_model() == "deepseek-v4-flash"
    assert _MODULE.get_global_model() == "deepseek-v4-flash"
    assert _MODULE.get_row_model() == "deepseek-v4-flash"


def test_table_semantic_legacy_overrides_still_work_without_canonical_envs(monkeypatch):
    monkeypatch.delenv("LLM_API_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("TABLE_SEMANTIC_LLM_URL", "https://legacy-table.example/v1/chat/completions")
    monkeypatch.setenv("TABLE_SEMANTIC_LLM_API_KEY", "legacy-table-key")
    monkeypatch.setenv("TABLE_SEMANTIC_CLASSIFIER_MODEL", "legacy-classifier")
    monkeypatch.setenv("TABLE_SEMANTIC_GLOBAL_MODEL", "legacy-global")
    monkeypatch.setenv("TABLE_SEMANTIC_ROW_MODEL", "legacy-row")

    assert _MODULE.get_table_semantic_llm_url() == "https://legacy-table.example/v1/chat/completions"
    assert _MODULE.get_table_semantic_llm_api_key() == "legacy-table-key"
    assert _MODULE.get_classifier_model() == "legacy-classifier"
    assert _MODULE.get_global_model() == "legacy-global"
    assert _MODULE.get_row_model() == "legacy-row"
