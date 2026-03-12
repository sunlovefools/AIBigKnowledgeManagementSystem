import asyncio
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

# Allow unit tests to run in environments without heavy ML deps installed.
if "torch" not in sys.modules:
    torch_stub = types.ModuleType("torch")

    class _NoGradContext:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    class _CudaStub:
        @staticmethod
        def is_available() -> bool:
            return False

        @staticmethod
        def empty_cache() -> None:
            return None

    class _MpsBackendStub:
        @staticmethod
        def is_available() -> bool:
            return False

    class _BackendsStub:
        mps = _MpsBackendStub()

    class _MpsModuleStub:
        @staticmethod
        def empty_cache() -> None:
            return None

    torch_stub.cuda = _CudaStub()
    torch_stub.backends = _BackendsStub()
    torch_stub.mps = _MpsModuleStub()
    torch_stub.device = lambda value: value
    torch_stub.no_grad = lambda: _NoGradContext()
    sys.modules["torch"] = torch_stub

if "sentence_transformers" not in sys.modules:
    sentence_transformers_stub = types.ModuleType("sentence_transformers")

    class _CrossEncoderPlaceholder:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("CrossEncoder placeholder should be patched in tests.")

    sentence_transformers_stub.CrossEncoder = _CrossEncoderPlaceholder
    sys.modules["sentence_transformers"] = sentence_transformers_stub

if "transformers" not in sys.modules:
    transformers_stub = types.ModuleType("transformers")

    class _AutoTokenizerPlaceholder:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            raise RuntimeError("AutoTokenizer placeholder should be patched in tests.")

    class _AutoModelPlaceholder:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            raise RuntimeError("AutoModelForCausalLM placeholder should be patched in tests.")

    transformers_stub.AutoTokenizer = _AutoTokenizerPlaceholder
    transformers_stub.AutoModelForCausalLM = _AutoModelPlaceholder
    sys.modules["transformers"] = transformers_stub

from app.service.rag.retrieval.reranker import bge_reranker as bge_module
from app.service.rag.retrieval.reranker import qwen_reranker as qwen_module
from app.service.rag.retrieval.reranker import service as reranker_service_module


class _FakeTensor:
    def __init__(self, data):
        self.data = data
        self.device = "cpu"

    def to(self, device):
        self.device = str(device)
        return self

    def detach(self):
        return self

    def float(self):
        return self

    def cpu(self):
        self.device = "cpu"
        return self

    def tolist(self):
        return self.data


class _FakeInnerModel:
    def __init__(self) -> None:
        self.moves: list[str] = []
        self.config = types.SimpleNamespace(pad_token_id=None)

    def to(self, device: str):
        self.moves.append(str(device))
        return self


class _FakeCrossTokenizer:
    def __init__(self) -> None:
        self.pad_token = "</s>"
        self.pad_token_id = 2
        self.eos_token = "</s>"
        self.eos_token_id = 2
        self.sep_token = None
        self.sep_token_id = None
        self.unk_token = "<unk>"
        self.unk_token_id = 0


class _NoPadCrossTokenizer:
    def __init__(self) -> None:
        self.pad_token = None
        self.pad_token_id = None
        self.eos_token = None
        self.eos_token_id = None
        self.sep_token = None
        self.sep_token_id = None
        self.unk_token = None
        self.unk_token_id = None


class _FakeCrossEncoder:
    created: list["_FakeCrossEncoder"] = []

    def __init__(self, model_name: str, device: str, **kwargs):
        self.model_name = model_name
        self.device = device
        self.kwargs = kwargs
        self.model = _FakeInnerModel()
        self.tokenizer = _FakeCrossTokenizer()
        self._target_device = device
        self.predict_calls: list[tuple[list[list[str]], dict]] = []
        _FakeCrossEncoder.created.append(self)

    def predict(self, pairs, **kwargs):
        self.predict_calls.append((pairs, kwargs))
        return [float(index) for index, _ in enumerate(pairs)]


class _FakeCrossEncoderNoPad(_FakeCrossEncoder):
    def __init__(self, model_name: str, device: str, **kwargs):
        super().__init__(model_name, device, **kwargs)
        self.tokenizer = _NoPadCrossTokenizer()


class _FakeCrossEncoderPaddingError(_FakeCrossEncoder):
    def predict(self, pairs, **kwargs):
        self.predict_calls.append((pairs, kwargs))
        batch_size = kwargs.get("batch_size", 32)
        if len(pairs) > 1 and batch_size > 1:
            raise RuntimeError("Cannot handle batch sizes > 1 if no padding token is defined.")
        return [float(index) for index, _ in enumerate(pairs)]


class _FakeQwenTokenizer:
    created: list["_FakeQwenTokenizer"] = []

    def __init__(self) -> None:
        self.pad_token = None
        self.pad_token_id = None
        self.eos_token = "</s>"
        self.eos_token_id = 2
        self.sep_token = None
        self.sep_token_id = None
        self.unk_token = "<unk>"
        self.unk_token_id = 0
        self.calls: list[list[str]] = []
        _FakeQwenTokenizer.created.append(self)

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        return cls()

    def encode(self, text: str, add_special_tokens: bool = False):
        if text == "yes":
            return [11]
        if text == "no":
            return [22]
        return [1]

    def __call__(self, texts, padding=True, truncation=True, return_tensors="pt"):
        self.calls.append(list(texts))
        batch = len(texts)
        return {
            "input_ids": _FakeTensor([[101, 102, 103] for _ in range(batch)]),
            "attention_mask": _FakeTensor([[1, 1, 1] for _ in range(batch)]),
            "raw_texts": list(texts),
        }


class _FakeQwenModel:
    created: list["_FakeQwenModel"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.config = types.SimpleNamespace(pad_token_id=None)
        self.moves: list[str] = []
        self.calls: list[dict] = []
        self.device = "cpu"
        _FakeQwenModel.created.append(self)

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        return cls(**kwargs)

    def eval(self):
        return self

    def to(self, device):
        self.moves.append(str(device))
        self.device = str(device)
        return self

    def __call__(self, **inputs):
        self.calls.append(inputs)
        texts = inputs.get("raw_texts", [])
        logits = []
        for text in texts:
            score = 5.0 if "HIGH_DOC" in text else 1.0
            seq_logits = [[0.0] * 32 for _ in range(3)]
            seq_logits[-1][11] = score
            seq_logits[-1][22] = 0.0
            logits.append(seq_logits)
        return types.SimpleNamespace(logits=_FakeTensor(logits))


class _FakeQwenModelRaise(_FakeQwenModel):
    def __call__(self, **inputs):
        self.calls.append(inputs)
        texts = inputs.get("raw_texts", [])
        if any("RAISE_DOC" in text for text in texts):
            raise RuntimeError("Qwen inference failed")
        return super().__call__(**inputs)


class TestRerankerConfig(unittest.TestCase):
    def setUp(self) -> None:
        _FakeCrossEncoder.created.clear()
        _FakeQwenTokenizer.created.clear()
        _FakeQwenModel.created.clear()

    def test_invalid_model_falls_back_to_default(self):
        stdout = io.StringIO()
        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "invalid/reranker", "RERANKER_SWAP_TO_RAM": "false"},
            clear=False,
        ):
            with patch.object(bge_module, "CrossEncoder", _FakeCrossEncoder):
                with patch.object(reranker_service_module, "detect_preferred_device", return_value="cpu"):
                    with redirect_stdout(stdout):
                        service = reranker_service_module.ZeRankerService()

        self.assertEqual(service.model_name, reranker_service_module.DEFAULT_RERANKER_MODEL)
        self.assertEqual(service.backend, "cross_encoder")
        self.assertIn("Unsupported RERANKER_MODEL", stdout.getvalue())

    def test_cross_encoder_no_padding_falls_back_to_batch_size_one(self):
        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "BAAI/bge-reranker-v2-m3", "RERANKER_SWAP_TO_RAM": "false"},
            clear=False,
        ):
            with patch.object(bge_module, "CrossEncoder", _FakeCrossEncoderNoPad):
                with patch.object(reranker_service_module, "detect_preferred_device", return_value="cpu"):
                    service = reranker_service_module.ZeRankerService()
                    asyncio.run(service.rerank_documents("query", ["doc-a", "doc-b"], top_k=2))

        created = _FakeCrossEncoder.created[0]
        _, kwargs = created.predict_calls[-1]
        self.assertEqual(kwargs.get("batch_size"), 1)

    def test_cross_encoder_runtime_padding_error_retries(self):
        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "BAAI/bge-reranker-v2-m3", "RERANKER_SWAP_TO_RAM": "false"},
            clear=False,
        ):
            with patch.object(bge_module, "CrossEncoder", _FakeCrossEncoderPaddingError):
                with patch.object(reranker_service_module, "detect_preferred_device", return_value="cpu"):
                    service = reranker_service_module.ZeRankerService()
                    result = asyncio.run(
                        service.rerank_documents("query", ["doc-a", "doc-b", "doc-c"], top_k=2)
                    )

        created = _FakeCrossEncoder.created[0]
        self.assertEqual(len(created.predict_calls), 3)  # warmup + first try + retry
        _, first_kwargs = created.predict_calls[1]
        _, retry_kwargs = created.predict_calls[2]
        self.assertNotEqual(first_kwargs.get("batch_size"), 1)
        self.assertEqual(retry_kwargs.get("batch_size"), 1)
        self.assertEqual(len(result), 2)

    def test_qwen_instruction_prompt_and_yes_no_scoring(self):
        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "Qwen/Qwen3-Reranker-0.6B", "RERANKER_SWAP_TO_RAM": "false"},
            clear=False,
        ):
            with patch.object(qwen_module, "AutoTokenizer", _FakeQwenTokenizer):
                with patch.object(qwen_module, "AutoModelForCausalLM", _FakeQwenModel):
                    with patch.object(reranker_service_module, "detect_preferred_device", return_value="cpu"):
                        service = reranker_service_module.ZeRankerService()
                        result = asyncio.run(
                            service.rerank_documents(
                                "Which document is better?",
                                ["LOW_DOC", "HIGH_DOC"],
                                top_k=2,
                            )
                        )

        self.assertEqual(service.backend, "qwen_causal_lm")
        self.assertEqual(result[0][0], "HIGH_DOC")

        tokenizer = _FakeQwenTokenizer.created[0]
        self.assertTrue(tokenizer.calls)
        last_batch = tokenizer.calls[-1]
        self.assertIn(
            "Rank documents by how well they answer the query.",
            last_batch[0],
        )
        self.assertIn("<Instruct>:", last_batch[0])
        self.assertIn("<Query>:", last_batch[0])
        self.assertIn("<Document>:", last_batch[0])

    def test_qwen_swap_to_ram_offloads_model(self):
        cache_clear_calls = {"count": 0}

        def _fake_empty_cache():
            cache_clear_calls["count"] += 1

        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "Qwen/Qwen3-Reranker-0.6B", "RERANKER_SWAP_TO_RAM": "true"},
            clear=False,
        ):
            with patch.object(qwen_module, "AutoTokenizer", _FakeQwenTokenizer):
                with patch.object(qwen_module, "AutoModelForCausalLM", _FakeQwenModel):
                    with patch.object(reranker_service_module, "detect_preferred_device", return_value="cuda"):
                        with patch.object(
                            reranker_service_module,
                            "clear_device_cache",
                            side_effect=lambda _device: _fake_empty_cache(),
                        ):
                            service = reranker_service_module.ZeRankerService()
                            asyncio.run(service.rerank_documents("query", ["LOW_DOC"], top_k=1))

        model = _FakeQwenModel.created[0]
        self.assertIn("cuda", model.moves)
        self.assertEqual(model.moves[-1], "cpu")
        self.assertEqual(service.active_device, "cpu")
        self.assertEqual(cache_clear_calls["count"], 1)

    def test_qwen_inference_error_returns_original_order(self):
        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "Qwen/Qwen3-Reranker-0.6B", "RERANKER_SWAP_TO_RAM": "false"},
            clear=False,
        ):
            with patch.object(qwen_module, "AutoTokenizer", _FakeQwenTokenizer):
                with patch.object(qwen_module, "AutoModelForCausalLM", _FakeQwenModelRaise):
                    with patch.object(reranker_service_module, "detect_preferred_device", return_value="cpu"):
                        service = reranker_service_module.ZeRankerService()
                        result = asyncio.run(
                            service.rerank_documents(
                                "query",
                                ["FIRST_DOC", "RAISE_DOC", "THIRD_DOC"],
                                top_k=2,
                            )
                        )

        self.assertEqual(result, [("FIRST_DOC", 0.0), ("RAISE_DOC", 0.0)])


if __name__ == "__main__":
    unittest.main()
