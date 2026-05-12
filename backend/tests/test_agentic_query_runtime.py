import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fake_aiohttp = types.ModuleType("aiohttp")
fake_aiohttp.ClientError = Exception
fake_aiohttp.ClientTimeout = lambda total: {"total": total}
fake_aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", fake_aiohttp)

from app.service.rag.agentic_query import llm_client, runtime, tools
from app.service.rag.agentic_query.config_loader import load_agentic_query_config
from app.service.rag.agentic_query.models import EvidenceItem


class _FakeParentCollection:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def find(self, filter=None, projection=None):
        _ = projection
        filter = filter or {}
        return [row for row in self.rows if self._matches(row, filter)]

    def _matches(self, row: dict, filter_doc: dict) -> bool:
        for key, expected in filter_doc.items():
            actual = self._get_path(row, key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in set(expected["$in"]):
                    return False
                continue
            if actual != expected:
                return False
        return True

    def _get_path(self, row: dict, dotted_path: str):
        current = row
        for part in dotted_path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current


def _parent_row(
    *,
    parent_id: str,
    file_id: str,
    file_name: str,
    chunk_number: int,
    content: str,
    user_id: str = "user-1",
) -> dict:
    return {
        "_id": parent_id,
        "value": {
            "page_content": content,
            "metadata": {
                "user_id": user_id,
                "file_metadata": {"file_id": file_id, "file_name": file_name},
                "parent_chunk_metadata": {"parent_chunk_number": chunk_number},
            },
            "type": "Document",
        },
    }


def _llm_sequence(responses: list[str]):
    state = {"index": 0}

    async def _fake_call_action_model(**_kwargs):
        index = state["index"]
        state["index"] += 1
        if index >= len(responses):
            return responses[-1], {}
        return responses[index], {}

    return _fake_call_action_model


class _FakeActionModelResult:
    def __init__(self, content: str, assistant_message: dict | None = None):
        self.content = content
        self.usage = {}
        self.assistant_message = assistant_message or {"role": "assistant", "content": content}

    def __iter__(self):
        yield self.content
        yield self.usage


def test_safe_json_object_ignores_trailing_provider_wrapper():
    raw_response = (
        '<LLM_RESPONSE>\n'
        '{"action":"provide_final_answer","arguments":{"answer":"A {nested-looking} answer",'
        '"citations":["COMP2001-Artificial-Intelligence-Methods.pdf"]}}'
        '</\uff5c\uff5cDSML\uff5c\uff5cparameter>\n'
        '</\uff5c\uff5cDSML\uff5c\uff5cinvoke>\n'
        '</LLM_RESPONSE>'
    )

    parsed = runtime._safe_json_object(raw_response)

    assert parsed["action"] == "provide_final_answer"
    assert parsed["arguments"]["answer"] == "A {nested-looking} answer"
    assert parsed["arguments"]["citations"] == [
        "COMP2001-Artificial-Intelligence-Methods.pdf"
    ]


def test_runtime_rejects_unknown_action_and_recovers_with_finish(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"unknown_tool","arguments":{}}',
                '{"action":"finish","arguments":{"answer":"Recovered answer","citations":[]}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return []

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Where is the refund period?",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )

    assert result.answer == "Recovered answer"
    assert result.termination_reason == "finished"
    # Seed retrieval is counted; unknown action should not trigger extra tool execution.
    assert result.tool_call_count == 1


def test_forced_finish_parses_json_before_trailing_provider_wrapper(monkeypatch):
    async def _fake_search_context_tool(**kwargs):
        kwargs["parent_doc_cache"]["p-ai"] = {
            "id": "p-ai",
            "metadata": {
                "user_id": "user-1",
                "file_metadata": {
                    "file_id": "file-ai",
                    "file_name": "COMP2001-Artificial-Intelligence-Methods.pdf",
                },
                "parent_chunk_metadata": {"parent_chunk_number": 1},
            },
            "page_content": "Module Code: COMP2001 - Level: 2 - Semester: Spring UK",
        }
        return [
            EvidenceItem(
                parent_id="p-ai",
                file_id="file-ai",
                file_name="COMP2001-Artificial-Intelligence-Methods.pdf",
                parent_chunk_number=1,
                snippet="Module Code: COMP2001 - Level: 2 - Semester: Spring UK",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"search_context","arguments":{"query":"repeat","top_k":1}}',
                (
                    '{"action":"provide_final_answer","arguments":{"answer":"COMP2001",'
                    '"citations":["COMP2001-Artificial-Intelligence-Methods.pdf"]}}'
                    '</\uff5c\uff5cDSML\uff5c\uff5cparameter>\n'
                    '</\uff5c\uff5cDSML\uff5c\uff5cinvoke>'
                ),
            ]
        ),
    )

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Find the module",
            user_id="user-1",
            included_file_ids=["file-ai"],
            max_steps=1,
        )
    )

    assert result.answer == "COMP2001"
    assert result.citations == ["COMP2001-Artificial-Intelligence-Methods.pdf"]
    assert result.termination_reason == "forced_finish_after_max_steps"


def test_search_context_tool_passes_none_scope_to_vector_search(monkeypatch):
    captured: dict[str, object] = {}

    fake_vectordb = types.ModuleType("app.vectordb.vectordb")

    async def _fake_search_and_retrieve_context(**kwargs):
        captured.update(kwargs)
        return []

    fake_vectordb.search_and_retrieve_context = _fake_search_and_retrieve_context
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    result = asyncio.run(
        tools.search_context_tool(
            query="refund period",
            top_k=8,
            user_id="user-1",
            included_file_ids=None,
            parent_doc_cache={},
        )
    )

    assert result == []
    assert captured["included_file_ids"] is None
    assert captured["user_id"] == "user-1"


def test_evidence_item_includes_structured_table_view():
    item = tools._build_evidence_item(
        {
            "id": "parent-table",
            "page_content": "| Item | Weight |\n| --- | --- |\n| Manual | 25% |",
            "metadata": {
                "user_id": "user-1",
                "file_metadata": {"file_id": "file-table", "file_name": "rubric.pdf"},
                "parent_chunk_metadata": {
                    "parent_chunk_number": 3,
                    "table_semantic": {
                        "section_name": "Software User Manual",
                        "general_description": "Assessment criteria table.",
                        "criteria_names": ["Developer documentation"],
                        "weights": ["25%"],
                        "structured_rows": [
                            {
                                "label": "Developer documentation",
                                "weights": ["25%"],
                                "cells": {
                                    "Item": "Documentation for software engineer / programmer",
                                    "Criteria": "Architecture, design, implementation.",
                                },
                            }
                        ],
                    },
                },
            },
        },
        query="software manual criteria",
    )

    assert item is not None
    assert "Section: Software User Manual" in item.structured_view
    assert "- Developer documentation | weights: 25%" in item.structured_view
    assert "Documentation for software engineer / programmer" in item.structured_view


def test_search_context_tool_prefers_unseen_parent_chunks(monkeypatch):
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")

    async def _fake_search_and_retrieve_context(**_kwargs):
        return [
            {
                "id": "parent-seen",
                "page_content": "Already inspected handbook evidence.",
                "metadata": {
                    "user_id": "user-1",
                    "file_metadata": {
                        "file_id": "file-handbook",
                        "file_name": "handbook.pdf",
                    },
                    "parent_chunk_metadata": {"parent_chunk_number": 1},
                },
                "type": "Document",
            },
            {
                "id": "parent-new",
                "page_content": "New rubric evidence.",
                "metadata": {
                    "user_id": "user-1",
                    "file_metadata": {
                        "file_id": "file-rubric",
                        "file_name": "rubric.pdf",
                    },
                    "parent_chunk_metadata": {"parent_chunk_number": 2},
                },
                "type": "Document",
            },
        ]

    fake_vectordb.search_and_retrieve_context = _fake_search_and_retrieve_context
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    cache = {"parent-seen": {"id": "parent-seen"}}
    result = asyncio.run(
        tools.search_context_tool(
            query="main report criteria",
            top_k=8,
            user_id="user-1",
            included_file_ids=None,
            parent_doc_cache=cache,
        )
    )

    assert [item.parent_id for item in result] == ["parent-new"]
    assert "parent-new" in cache


def test_search_context_tool_falls_back_when_all_parent_chunks_seen(monkeypatch):
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")

    async def _fake_search_and_retrieve_context(**_kwargs):
        return [
            {
                "id": "parent-seen",
                "page_content": "Only already inspected evidence is still relevant.",
                "metadata": {
                    "user_id": "user-1",
                    "file_metadata": {
                        "file_id": "file-handbook",
                        "file_name": "handbook.pdf",
                    },
                    "parent_chunk_metadata": {"parent_chunk_number": 1},
                },
                "type": "Document",
            }
        ]

    fake_vectordb.search_and_retrieve_context = _fake_search_and_retrieve_context
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    cache = {"parent-seen": {"id": "parent-seen"}}
    result = asyncio.run(
        tools.search_context_tool(
            query="main report criteria",
            top_k=8,
            user_id="user-1",
            included_file_ids=None,
            parent_doc_cache=cache,
        )
    )

    assert [item.parent_id for item in result] == ["parent-seen"]
    assert cache["parent-seen"]["_agentic_query_snippet"]


def test_fetch_file_context_tool_returns_ordered_scoped_parent_chunks(monkeypatch):
    rows = [
        _parent_row(
            parent_id="parent-2",
            file_id="file-random",
            file_name="Random Stories.pdf",
            chunk_number=2,
            content="FACT 3 - Mira's Orange Mishap",
        ),
        _parent_row(
            parent_id="parent-0",
            file_id="file-random",
            file_name="Random Stories.pdf",
            chunk_number=0,
            content="FACT 1 - Suri's Watering Incident",
        ),
        _parent_row(
            parent_id="parent-1",
            file_id="file-random",
            file_name="Random Stories.pdf",
            chunk_number=1,
            content="FACT 2 - Ali's Chocolate Fall",
        ),
        _parent_row(
            parent_id="parent-other",
            file_id="file-other",
            file_name="Other.pdf",
            chunk_number=0,
            content="Other content",
        ),
    ]
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=_FakeParentCollection(rows))
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    cache: dict[str, dict] = {}
    result = asyncio.run(
        tools.fetch_file_context_tool(
            file_id="file-random",
            file_name=None,
            max_chunks=20,
            user_id="user-1",
            included_file_ids=["file-random"],
            parent_doc_cache=cache,
        )
    )

    assert [item.parent_chunk_number for item in result] == [0, 1, 2]
    assert [item.parent_id for item in result] == ["parent-0", "parent-1", "parent-2"]
    assert set(cache) == {"parent-0", "parent-1", "parent-2"}


def test_find_inventory_records_tool_returns_all_matching_scoped_records(monkeypatch):
    rows = [
        _parent_row(
            parent_id="p-ai",
            file_id="file-ai",
            file_name="COMP2001-Artificial-Intelligence-Methods.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2001 - Level: 2 - Semester: Spring UK",
        ),
        _parent_row(
            parent_id="p-afp",
            file_id="file-afp",
            file_name="COMP2003-Advanced-Functional-Programming.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2003 - Level: 2 - Semester: Spring UK",
        ),
        _parent_row(
            parent_id="p-hci",
            file_id="file-hci",
            file_name="COMP2004-Introduction-to-Human-Computer-Interaction.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2004 - Level: 2 - Semester: Spring UK",
        ),
        _parent_row(
            parent_id="p-cpp",
            file_id="file-cpp",
            file_name="COMP2006-C++-Programming.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2006 - Level: 2 - Semester: Spring UK",
        ),
        _parent_row(
            parent_id="p-lac",
            file_id="file-lac",
            file_name="COMP2012-Languages-and-Computation.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2012 - Level: 2 - Semester: Spring UK",
        ),
        _parent_row(
            parent_id="p-ds",
            file_id="file-ds",
            file_name="COMP2014-Distributed-Systems.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2014 - Level: 2 - Semester: Spring UK",
        ),
        _parent_row(
            parent_id="p-adse",
            file_id="file-adse",
            file_name="COMP2054-Algorithms-Data-Structures-and-Efficiency.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2054 - Level: 2 - Semester: Spring UK",
        ),
        _parent_row(
            parent_id="p-segp",
            file_id="file-segp",
            file_name="COMP2002-Software-Engineering-Group-Project.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2002 - Level: 2 - Semester: Full Year",
        ),
        _parent_row(
            parent_id="p-autumn",
            file_id="file-autumn",
            file_name="COMP2007-Operating-Systems-and-Concurrency.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP2007 - Level: 2 - Semester: Autumn UK",
        ),
        _parent_row(
            parent_id="p-l3",
            file_id="file-l3",
            file_name="COMP3001-Something.pdf",
            chunk_number=1,
            content="## Module Overview - Module Code: COMP3001 - Level: 3 - Semester: Spring UK",
        ),
    ]
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=_FakeParentCollection(rows))
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    cache: dict[str, dict] = {}
    result = asyncio.run(
        tools.find_inventory_records_tool(
            query="Find all the Y2 Spring modules and their assessment weightage",
            user_id="user-1",
            included_file_ids=[
                "file-ai",
                "file-afp",
                "file-hci",
                "file-cpp",
                "file-lac",
                "file-ds",
                "file-adse",
                "file-segp",
                "file-autumn",
            ],
            parent_doc_cache=cache,
        )
    )

    assert [item.file_name for item in result] == [
        "COMP2001-Artificial-Intelligence-Methods.pdf",
        "COMP2002-Software-Engineering-Group-Project.pdf",
        "COMP2003-Advanced-Functional-Programming.pdf",
        "COMP2004-Introduction-to-Human-Computer-Interaction.pdf",
        "COMP2006-C++-Programming.pdf",
        "COMP2012-Languages-and-Computation.pdf",
        "COMP2014-Distributed-Systems.pdf",
        "COMP2054-Algorithms-Data-Structures-and-Efficiency.pdf",
    ]
    assert set(cache) == {
        "p-ai",
        "p-afp",
        "p-hci",
        "p-cpp",
        "p-lac",
        "p-ds",
        "p-adse",
        "p-segp",
    }


def test_find_inventory_records_tool_supports_non_module_inventory_queries(monkeypatch):
    rows = [
        _parent_row(
            parent_id="p-security",
            file_id="file-security",
            file_name="Security-Policy.pdf",
            chunk_number=1,
            content="Policy Overview - Security policy for account access.",
        ),
        _parent_row(
            parent_id="p-retention",
            file_id="file-retention",
            file_name="Retention-Policy.pdf",
            chunk_number=1,
            content="Policy Overview - Data retention policy for archived records.",
        ),
        _parent_row(
            parent_id="p-guide",
            file_id="file-guide",
            file_name="Setup-Guide.pdf",
            chunk_number=1,
            content="Guide Overview - Local development setup instructions.",
        ),
    ]
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=_FakeParentCollection(rows))
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    result = asyncio.run(
        tools.find_inventory_records_tool(
            query="List all policy records",
            user_id="user-1",
            included_file_ids=None,
            parent_doc_cache={},
        )
    )

    assert [item.file_name for item in result] == [
        "Retention-Policy.pdf",
        "Security-Policy.pdf",
    ]


def test_fetch_file_context_tool_biases_large_single_chunk_to_query_terms(monkeypatch):
    long_prefix = " ".join(f"CV criteria filler {index}" for index in range(180))
    main_report_row = (
        "Main Report Project Background & Understanding 12% "
        "Requirements & Critical Analysis 15% Project Management & progress 15% "
        "Reflection 12% Style 6%"
    )
    rows = [
        _parent_row(
            parent_id="parent-rubric",
            file_id="file-rubric",
            file_name="groupProjectRubric2025-2026.pdf",
            chunk_number=0,
            content=f"{long_prefix} {main_report_row}",
        )
    ]
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=_FakeParentCollection(rows))
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    result = asyncio.run(
        tools.fetch_file_context_tool(
            file_id="file-rubric",
            file_name=None,
            max_chunks=20,
            user_id="user-1",
            included_file_ids=["file-rubric"],
            parent_doc_cache={},
            query="Rubric里面关于main report的标准有哪些",
        )
    )

    assert len(result) == 1
    assert "Main Report Project Background" in result[0].snippet
    assert "Reflection 12%" in result[0].snippet


def test_fetch_file_context_tool_keeps_long_main_report_table_section(monkeypatch):
    long_prefix = " ".join(f"Earlier rubric content {index}" for index in range(260))
    main_report_section = (
        "Main Report Project Background & Understanding 12% Criteria understanding originality related work. "
        "Requirements & Critical Analysis 15% Criteria requirements prioritisation scope reductions methods user surveys. "
        "Project Management & progress 15% Criteria agile tools team skills supporting documentation work ethic. "
        + " ".join(f"middle filler {index}" for index in range(130))
        + " Reflection 12% Criteria technical reflection methods choices difficulties future directions realisation reflection achievements weaknesses caveats management reflection posterior evaluation lessons learned contingency planning. "
        + "Style 6% Criteria content related length depth motivation presentation accessibility clarity structure narrative grammar spelling articulation jargon visuals graphics formatting layout."
    )
    rows = [
        _parent_row(
            parent_id="parent-rubric",
            file_id="file-rubric",
            file_name="rubric_table.docx",
            chunk_number=0,
            content=f"{long_prefix} {main_report_section}",
        )
    ]
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=_FakeParentCollection(rows))
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    result = asyncio.run(
        tools.fetch_file_context_tool(
            file_id="file-rubric",
            file_name=None,
            max_chunks=20,
            user_id="user-1",
            included_file_ids=["file-rubric"],
            parent_doc_cache={},
            query="Rubric里面关于main report的标准有哪些",
        )
    )

    assert len(result) == 1
    assert "Reflection 12%" in result[0].snippet
    assert "Style 6%" in result[0].snippet


def test_search_files_tool_finds_filename_in_scope(monkeypatch):
    rows = [
        _parent_row(
            parent_id="parent-random",
            file_id="file-random",
            file_name="Random Stories.pdf",
            chunk_number=0,
            content="FACT 1 - Suri's Watering Incident",
        ),
        _parent_row(
            parent_id="parent-blocked",
            file_id="file-blocked",
            file_name="Random Blocked.pdf",
            chunk_number=0,
            content="Blocked",
        ),
    ]
    fake_vectordb = types.ModuleType("app.vectordb.vectordb")
    fake_vectordb.PARENT_STORE = types.SimpleNamespace(collection=_FakeParentCollection(rows))
    monkeypatch.setitem(sys.modules, "app.vectordb.vectordb", fake_vectordb)

    result = asyncio.run(
        tools.search_files_tool(
            query="random file",
            limit=5,
            user_id="user-1",
            included_file_ids=["file-random"],
        )
    )

    assert len(result) == 1
    assert result[0].file_id == "file-random"
    assert result[0].file_name == "Random Stories.pdf"


def test_runtime_preserves_none_scope_for_all_collections_seed(monkeypatch):
    captured_scopes: list[object] = []

    async def _fake_search_context_tool(**kwargs):
        captured_scopes.append(kwargs.get("included_file_ids"))
        return []

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            ['{"action":"finish","arguments":{"answer":"Done","citations":[]}}']
        ),
    )

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Q1",
            user_id="user-1",
            included_file_ids=None,
            max_steps=1,
        )
    )

    assert result.termination_reason == "finished"
    assert captured_scopes == [None]


def test_runtime_preserves_provider_reasoning_content_in_transcript(monkeypatch):
    captured_message_batches: list[list[dict]] = []

    async def _fake_call_action_model(**kwargs):
        captured_message_batches.append([dict(message) for message in kwargs["messages"]])
        if len(captured_message_batches) == 1:
            content = '{"action":"search_context","arguments":{"query":"refund","top_k":2}}'
            return _FakeActionModelResult(
                content,
                {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": "thinking from provider",
                },
            )
        return _FakeActionModelResult(
            '{"action":"finish","arguments":{"answer":"Done","citations":[]}}'
        )

    async def _fake_search_context_tool(**_kwargs):
        return []

    monkeypatch.setattr(runtime.llm_client, "call_action_model", _fake_call_action_model)
    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Q1",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )

    assert result.termination_reason == "finished"
    assert len(captured_message_batches) == 2
    assert any(
        message.get("role") == "assistant"
        and message.get("reasoning_content") == "thinking from provider"
        for message in captured_message_batches[1]
    )


def test_agentic_query_llm_client_disables_deepseek_thinking_and_preserves_reasoning(monkeypatch):
    captured_payload: dict[str, object] = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

        async def text(self):
            return ""

        async def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"action":"finish","arguments":{"answer":"Done","citations":[]}}',
                            "reasoning_content": "reasoning that provider requires in follow-up turns",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }

    class _FakeSession:
        def post(self, url, json, headers, timeout):
            _ = url, headers, timeout
            captured_payload.update(json)
            return _FakeResponse()

    monkeypatch.setenv("AGENTIC_QUERY_LLM_KEY", "test-key")
    monkeypatch.delenv("AGENTIC_QUERY_LLM_THINKING", raising=False)
    monkeypatch.delenv("MOD_AGENT_LLM_THINKING", raising=False)
    monkeypatch.delenv("AGENTIC_QUERY_LLM_URL", raising=False)
    monkeypatch.delenv("MOD_AGENT_LLM_URL", raising=False)
    monkeypatch.delenv("AGENTIC_QUERY_LLM_MODEL", raising=False)
    monkeypatch.delenv("MOD_AGENT_LLM_MODEL", raising=False)
    if "aiohttp" in sys.modules:
        monkeypatch.setattr(
            sys.modules["aiohttp"],
            "ClientTimeout",
            lambda total: {"total": total},
            raising=False,
        )

    result = asyncio.run(
        llm_client.call_action_model(
            messages=[{"role": "user", "content": "Return finish JSON."}],
            session=_FakeSession(),
        )
    )

    assert captured_payload["thinking"] == {"type": "disabled"}
    assert captured_payload["temperature"] == 0
    assert result.content.startswith('{"action":"finish"')
    assert result.usage == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    assert (
        result.assistant_message["reasoning_content"]
        == "reasoning that provider requires in follow-up turns"
    )


def test_agentic_query_llm_client_prefers_canonical_llm_envs(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_THINKING", "disabled")
    monkeypatch.setenv("AGENTIC_QUERY_LLM_URL", "https://legacy-query.example/v1/chat/completions")
    monkeypatch.setenv("AGENTIC_QUERY_LLM_KEY", "legacy-query-key")
    monkeypatch.setenv("AGENTIC_QUERY_LLM_MODEL", "legacy-query-model")

    url, api_key, model, thinking = llm_client._resolve_runtime_config()

    assert url == "https://api.deepseek.com/chat/completions"
    assert api_key == "canonical-key"
    assert model == "deepseek-v4-flash"
    assert thinking == "disabled"


def test_runtime_enforces_max_steps(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"search_context","arguments":{"query":"q","top_k":2}}',
                '{"action":"search_context","arguments":{"query":"q","top_k":2}}',
                '{"action":"search_context","arguments":{"query":"q","top_k":2}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return []

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Tell me about refund policy",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )

    assert result.termination_reason == "max_steps_exceeded"
    assert result.answer == "No answer found in the provided context."


def test_runtime_default_timeout_is_500_seconds():
    assert runtime._DEFAULT_TIMEOUT_SECONDS == 500.0


def test_runtime_reads_reference_only_on_demand(monkeypatch):
    calls = {"count": 0}

    def _fake_read_reference_content(_config, _skill_name, _ref_id, *, max_chars: int = 3000):
        _ = max_chars, _skill_name
        calls["count"] += 1
        return "Reference content"

    monkeypatch.setattr(runtime.tools, "read_reference_content", _fake_read_reference_content)

    async def _fake_search_context_tool(**_kwargs):
        return []

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            ['{"action":"finish","arguments":{"answer":"Done","citations":[]}}']
        ),
    )
    _ = asyncio.run(
        runtime.run_agentic_query(
            user_query="Q1",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=1,
        )
    )
    assert calls["count"] == 0

    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"read_reference","arguments":{"skill_name":"agentic-query","ref_id":"answer_examples"}}',
                '{"action":"finish","arguments":{"answer":"Done","citations":[]}}',
            ]
        ),
    )
    _ = asyncio.run(
        runtime.run_agentic_query(
            user_query="Q2",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )
    assert calls["count"] == 1


def test_config_loads_system_prompt_and_skill_metadata_without_body():
    load_agentic_query_config.cache_clear()
    config = load_agentic_query_config()

    assert "Agentic Query Runtime" in config.system_prompt
    assert "## Working method" not in config.system_prompt
    assert "agentic-query" in config.skill_registry
    metadata = config.skill_registry["agentic-query"]
    assert metadata.description
    assert "load_answering_instructions" in metadata.allowed_tools
    assert "answer_examples" in metadata.reference_ids


def test_runtime_loads_skill_only_when_requested(monkeypatch):
    calls = {"count": 0}

    def _fake_load_skill_tool(**kwargs):
        calls["count"] += 1
        _ = kwargs
        return {
            "skill_name": "agentic-query",
            "frontmatter": {"name": "agentic-query"},
            "body": "Full skill body",
            "references": ["answer_examples"],
            "cached": False,
        }

    monkeypatch.setattr(runtime.tools, "load_skill_tool", _fake_load_skill_tool)

    async def _fake_search_context_tool(**_kwargs):
        return []

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            ['{"action":"finish","arguments":{"answer":"Done","citations":[]}}']
        ),
    )
    _ = asyncio.run(
        runtime.run_agentic_query(
            user_query="Q1",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=1,
        )
    )
    assert calls["count"] == 0

    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"load_skill","arguments":{"skill_name":"agentic-query"}}',
                '{"action":"finish","arguments":{"answer":"Done","citations":[]}}',
            ]
        ),
    )
    _ = asyncio.run(
        runtime.run_agentic_query(
            user_query="Q2",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )
    assert calls["count"] == 1


def test_runtime_sends_persistent_messages_transcript(monkeypatch):
    captured_messages: list[list[dict]] = []

    async def _fake_call_action_model(**kwargs):
        captured_messages.append([dict(item) for item in kwargs["messages"]])
        if len(captured_messages) == 1:
            return '{"action":"search_context","arguments":{"query":"refund","top_k":2}}', {}
        return '{"action":"finish","arguments":{"answer":"Done","citations":["policy.md"]}}', {}

    monkeypatch.setattr(runtime.llm_client, "call_action_model", _fake_call_action_model)

    async def _fake_search_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id="parent-1",
                file_id="file-a",
                file_name="policy.md",
                parent_chunk_number=1,
                snippet="Refund period is 30 days.",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="What is the refund period?",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )

    assert result.termination_reason == "finished"
    assert len(captured_messages) == 2
    assert any(message.get("role") == "tool" for message in captured_messages[0])
    assert len(captured_messages[1]) > len(captured_messages[0])
    assert any(
        message.get("role") == "assistant" and "search_context" in str(message.get("content"))
        for message in captured_messages[1]
    )


def test_runtime_executes_fetch_file_context_action(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"fetch_file_context","arguments":{"file_id":"file-random","max_chunks":20}}',
                '{"action":"finish","arguments":{"answer":"Summarized all five facts.","citations":["Random Stories.pdf"]}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**kwargs):
        parent_doc_cache = kwargs["parent_doc_cache"]
        parent_doc_cache["parent-0"] = {
            "id": "parent-0",
            "page_content": "FACT 1 - Suri's Watering Incident",
            "metadata": {
                "user_id": "user-1",
                "file_metadata": {"file_id": "file-random", "file_name": "Random Stories.pdf"},
                "parent_chunk_metadata": {"parent_chunk_number": 0},
            },
        }
        return [
            EvidenceItem(
                parent_id="parent-0",
                file_id="file-random",
                file_name="Random Stories.pdf",
                parent_chunk_number=0,
                snippet="FACT 1 - Suri's Watering Incident",
            )
        ]

    async def _fake_fetch_file_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id=f"parent-{index}",
                file_id="file-random",
                file_name="Random Stories.pdf",
                parent_chunk_number=index,
                snippet=f"FACT {index + 1}",
            )
            for index in range(5)
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)
    monkeypatch.setattr(runtime.tools, "fetch_file_context_tool", _fake_fetch_file_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Summarises the random facts file",
            user_id="user-1",
            included_file_ids=["file-random"],
            max_steps=2,
        )
    )

    assert result.termination_reason == "finished"
    assert result.answer == "Summarized all five facts."
    assert result.citations == ["Random Stories.pdf"]


def test_runtime_auto_seeds_inventory_records_for_exhaustive_query(monkeypatch):
    async def _fake_search_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id="p-cpp",
                file_id="file-cpp",
                file_name="COMP2006-C++-Programming.pdf",
                parent_chunk_number=1,
                snippet="Module Code: COMP2006 - Level: 2 - Semester: Spring UK",
            )
        ]

    async def _fake_find_inventory_records_tool(**kwargs):
        kwargs["parent_doc_cache"]["p-ai"] = {"id": "p-ai"}
        return [
            EvidenceItem(
                parent_id="p-ai",
                file_id="file-ai",
                file_name="COMP2001-Artificial-Intelligence-Methods.pdf",
                parent_chunk_number=1,
                snippet="Module Code: COMP2001 - Level: 2 - Semester: Spring UK",
            ),
            EvidenceItem(
                parent_id="p-cpp",
                file_id="file-cpp",
                file_name="COMP2006-C++-Programming.pdf",
                parent_chunk_number=1,
                snippet="Module Code: COMP2006 - Level: 2 - Semester: Spring UK",
            ),
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)
    monkeypatch.setattr(runtime.tools, "find_inventory_records_tool", _fake_find_inventory_records_tool)
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                (
                    '{"action":"finish","arguments":{"answer":"COMP2001 and COMP2006",'
                    '"citations":["COMP2001-Artificial-Intelligence-Methods.pdf",'
                    '"COMP2006-C++-Programming.pdf"]}}'
                )
            ]
        ),
    )

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="List down all the module in Y2 Spring",
            user_id="user-1",
            included_file_ids=["file-ai", "file-cpp"],
            max_steps=1,
        )
    )

    assert result.answer == "COMP2001 and COMP2006"
    assert result.citations == [
        "COMP2001-Artificial-Intelligence-Methods.pdf",
        "COMP2006-C++-Programming.pdf",
    ]
    assert result.tool_call_count == 2


def test_runtime_rejects_inventory_answer_with_extra_semantic_record(monkeypatch):
    async def _fake_search_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id="p-compiler",
                file_id="file-compiler",
                file_name="COMP3095-Compiler-Design-and-Implementation.pdf",
                parent_chunk_number=7,
                snippet="Assessment table for COMP3095.",
            )
        ]

    async def _fake_find_inventory_records_tool(**kwargs):
        kwargs["parent_doc_cache"]["p-ai"] = {
            "id": "p-ai",
            "metadata": {
                "file_metadata": {
                    "file_name": "COMP2001-Artificial-Intelligence-Methods.pdf"
                }
            },
            "page_content": "Module Code: COMP2001 - Level: 2 - Semester: Spring UK",
        }
        kwargs["parent_doc_cache"]["p-cpp"] = {
            "id": "p-cpp",
            "metadata": {
                "file_metadata": {
                    "file_name": "COMP2006-C++-Programming.pdf"
                }
            },
            "page_content": "Module Code: COMP2006 - Level: 2 - Semester: Spring UK",
        }
        return [
            EvidenceItem(
                parent_id="p-ai",
                file_id="file-ai",
                file_name="COMP2001-Artificial-Intelligence-Methods.pdf",
                parent_chunk_number=1,
                snippet="Module Code: COMP2001 - Level: 2 - Semester: Spring UK",
            ),
            EvidenceItem(
                parent_id="p-cpp",
                file_id="file-cpp",
                file_name="COMP2006-C++-Programming.pdf",
                parent_chunk_number=1,
                snippet="Module Code: COMP2006 - Level: 2 - Semester: Spring UK",
            ),
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)
    monkeypatch.setattr(runtime.tools, "find_inventory_records_tool", _fake_find_inventory_records_tool)
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                (
                    '{"action":"finish","arguments":{"answer":"COMP2001 and COMP3095",'
                    '"citations":["COMP2001-Artificial-Intelligence-Methods.pdf",'
                    '"COMP3095-Compiler-Design-and-Implementation.pdf"]}}'
                ),
                (
                    '{"action":"finish","arguments":{"answer":"COMP2001 and COMP2006",'
                    '"citations":["COMP2001-Artificial-Intelligence-Methods.pdf",'
                    '"COMP2006-C++-Programming.pdf"]}}'
                ),
            ]
        ),
    )

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="Find all the Y2 Spring modules and their assessment weightage",
            user_id="user-1",
            included_file_ids=["file-ai", "file-cpp", "file-compiler"],
            max_steps=2,
        )
    )

    assert result.answer == "COMP2001 and COMP2006"
    assert result.citations == [
        "COMP2001-Artificial-Intelligence-Methods.pdf",
        "COMP2006-C++-Programming.pdf",
    ]


def test_runtime_normalizes_citations_to_scoped_evidence(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"search_context","arguments":{"query":"refund","top_k":2}}',
                '{"action":"finish","arguments":{"answer":"Final","citations":["policy.md","BAD.md","POLICY.MD"]}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id="parent-1",
                file_id="file-a",
                file_name="policy.md",
                parent_chunk_number=1,
                snippet="Policy snippet",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="What is refund policy?",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
        )
    )

    assert result.answer == "Final"
    assert result.citations == ["policy.md"]


def test_runtime_forced_finish_uses_cached_evidence_after_repeated_search(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                '{"action":"search_context","arguments":{"query":"speed of light","top_k":5}}',
                '{"action":"search_context","arguments":{"query":"speed of light","top_k":5}}',
                '{"action":"finish","arguments":{"answer":"The speed of light in a vacuum is exactly 0 meters per second.","citations":["Random Facts.pdf"]}}',
            ]
        ),
    )

    async def _fake_search_context_tool(**kwargs):
        parent_doc_cache = kwargs["parent_doc_cache"]
        parent_doc_cache["parent-speed"] = {
            "id": "parent-speed",
            "page_content": "Science facts. 4. The speed of light in a vacuum is exactly 0 meters per second.",
            "_agentic_query_snippet": "4. The speed of light in a vacuum is exactly 0 meters per second.",
            "metadata": {
                "user_id": "user-1",
                "file_metadata": {
                    "file_id": "file-speed",
                    "file_name": "Random Facts.pdf",
                },
                "parent_chunk_metadata": {
                    "parent_chunk_number": 1,
                },
            },
        }
        return [
            EvidenceItem(
                parent_id="parent-speed",
                file_id="file-speed",
                file_name="Random Facts.pdf",
                parent_chunk_number=1,
                snippet="4. The speed of light in a vacuum is exactly 0 meters per second.",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="What is the speed of light",
            user_id="user-1",
            included_file_ids=["file-speed"],
            max_steps=2,
        )
    )

    assert result.termination_reason == "forced_finish_after_max_steps"
    assert result.answer == "The speed of light in a vacuum is exactly 0 meters per second."
    assert result.citations == ["Random Facts.pdf"]


def test_runtime_emits_structured_step_trace_metadata(monkeypatch):
    monkeypatch.setattr(
        runtime.llm_client,
        "call_action_model",
        _llm_sequence(
            [
                (
                    '{"action":"search_context","arguments":{"query":"refund","top_k":2},'
                    '"intent":"Find policy evidence","decision":"If weak, fetch parent chunk"}'
                ),
                (
                    '{"action":"finish","arguments":{"answer":"Refund period is 30 days.","citations":["policy.md"]},'
                    '"intent":"Enough evidence to answer","decision":"Stop"}'
                ),
            ]
        ),
    )

    async def _fake_search_context_tool(**_kwargs):
        return [
            EvidenceItem(
                parent_id="parent-1",
                file_id="file-a",
                file_name="policy.md",
                parent_chunk_number=1,
                snippet="Refund period is 30 days.",
            )
        ]

    monkeypatch.setattr(runtime.tools, "search_context_tool", _fake_search_context_tool)

    progress_events: list[dict] = []

    async def _collect_progress(event):
        progress_events.append(dict(event))

    result = asyncio.run(
        runtime.run_agentic_query(
            user_query="What is the refund period?",
            user_id="user-1",
            included_file_ids=["file-a"],
            max_steps=2,
            progress_callback=_collect_progress,
        )
    )

    assert result.termination_reason == "finished"
    step_started = [
        event
        for event in progress_events
        if event.get("stage") == "agentic_query_step"
        and event.get("status") == "started"
        and isinstance(event.get("metadata"), dict)
        and event["metadata"].get("action") == "search_relevant_chunks"
    ]
    assert step_started, "Expected started step event for search_relevant_chunks."
    assert step_started[0]["metadata"].get("intent") == "Find policy evidence"
    assert step_started[0]["metadata"].get("decision") == "If weak, fetch parent chunk"
    assert step_started[0]["metadata"].get("tool") == "search_relevant_chunks"

    step_completed = [
        event
        for event in progress_events
        if event.get("stage") == "agentic_query_step"
        and event.get("status") == "completed"
        and isinstance(event.get("metadata"), dict)
        and event["metadata"].get("action") == "search_relevant_chunks"
    ]
    assert step_completed, "Expected completed step event for search_relevant_chunks."
    assert isinstance(step_completed[0]["metadata"].get("argumentsPreview"), str)
    assert "search_relevant_chunks returned" in str(step_completed[0]["metadata"].get("observation"))
