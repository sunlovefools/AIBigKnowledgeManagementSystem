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

# Allow running unit tests in environments without heavy ML deps installed.
if "torch" not in sys.modules:
    torch_stub = types.ModuleType("torch")

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
    sys.modules["torch"] = torch_stub

if "sentence_transformers" not in sys.modules:
    sentence_transformers_stub = types.ModuleType("sentence_transformers")

    class _CrossEncoderPlaceholder:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("CrossEncoder placeholder should be patched in tests.")

    sentence_transformers_stub.CrossEncoder = _CrossEncoderPlaceholder
    sys.modules["sentence_transformers"] = sentence_transformers_stub

from app.service.rag.retrieval import reranker as reranker_module


class _FakeInnerModel:
    def __init__(self) -> None:
        self.moves: list[str] = []
        self.config = types.SimpleNamespace(pad_token_id=None)

    def to(self, device: str):
        self.moves.append(str(device))
        return self


class _FakeTokenizer:
    def __init__(self) -> None:
        self.pad_token = "</s>"
        self.pad_token_id = 2
        self.eos_token = "</s>"
        self.eos_token_id = 2
        self.sep_token = None
        self.sep_token_id = None
        self.unk_token = "<unk>"
        self.unk_token_id = 0


class _NoPadTokenizer:
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
        self.tokenizer = _FakeTokenizer()
        self._target_device = device
        self.predict_calls: list[tuple[list[list[str]], dict]] = []
        _FakeCrossEncoder.created.append(self)

    def predict(self, pairs, **kwargs):
        self.predict_calls.append((pairs, kwargs))
        return [float(index) for index, _ in enumerate(pairs)]


class _FakeCrossEncoderNoPad(_FakeCrossEncoder):
    def __init__(self, model_name: str, device: str, **kwargs):
        super().__init__(model_name, device, **kwargs)
        self.tokenizer = _NoPadTokenizer()


class _FakeCrossEncoderBatchPaddingError(_FakeCrossEncoder):
    def predict(self, pairs, **kwargs):
        self.predict_calls.append((pairs, kwargs))
        batch_size = kwargs.get("batch_size", 32)
        if len(pairs) > 1 and batch_size > 1:
            raise RuntimeError("Cannot handle batch sizes > 1 if no padding token is defined.")
        return [float(index) for index, _ in enumerate(pairs)]


class TestRerankerConfig(unittest.TestCase):
    def setUp(self) -> None:
        _FakeCrossEncoder.created.clear()

    def test_supported_models_are_loaded_from_env(self):
        for model_name in ("BAAI/bge-reranker-v2-m3", "Qwen/Qwen3-Reranker-0.6B"):
            with patch.dict(
                os.environ,
                {"RERANKER_MODEL": model_name, "RERANKER_SWAP_TO_RAM": "false"},
                clear=False,
            ):
                with patch.object(reranker_module, "CrossEncoder", _FakeCrossEncoder):
                    with patch.object(reranker_module, "_detect_preferred_device", return_value="cpu"):
                        service = reranker_module.ZeRankerService()

            self.assertEqual(service.model_name, model_name)
            created = _FakeCrossEncoder.created[0]
            self.assertEqual(created.model_name, model_name)

            if model_name == "Qwen/Qwen3-Reranker-0.6B":
                self.assertTrue(created.kwargs["trust_remote_code"])
                self.assertEqual(created.kwargs["model_kwargs"]["torch_dtype"], "auto")
            else:
                self.assertNotIn("trust_remote_code", created.kwargs)
            self.assertEqual(created.model.config.pad_token_id, 2)

            _FakeCrossEncoder.created.clear()

    def test_invalid_model_falls_back_to_default(self):
        stdout = io.StringIO()
        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "invalid/reranker", "RERANKER_SWAP_TO_RAM": "false"},
            clear=False,
        ):
            with patch.object(reranker_module, "CrossEncoder", _FakeCrossEncoder):
                with patch.object(reranker_module, "_detect_preferred_device", return_value="cpu"):
                    with redirect_stdout(stdout):
                        service = reranker_module.ZeRankerService()

        self.assertEqual(service.model_name, reranker_module.DEFAULT_RERANKER_MODEL)
        self.assertIn("Unsupported RERANKER_MODEL", stdout.getvalue())

    def test_swap_to_ram_offloads_and_clears_cuda_cache(self):
        cache_clear_calls = {"count": 0}

        def _fake_empty_cache():
            cache_clear_calls["count"] += 1

        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "BAAI/bge-reranker-v2-m3", "RERANKER_SWAP_TO_RAM": "yes"},
            clear=False,
        ):
            with patch.object(reranker_module, "CrossEncoder", _FakeCrossEncoder):
                with patch.object(reranker_module, "_detect_preferred_device", return_value="cuda"):
                    with patch.object(reranker_module.torch.cuda, "is_available", return_value=True):
                        with patch.object(reranker_module.torch.cuda, "empty_cache", side_effect=_fake_empty_cache):
                            service = reranker_module.ZeRankerService()
                            result = asyncio.run(
                                service.rerank_documents("query", ["doc-a", "doc-b"], top_k=2)
                            )

        created = _FakeCrossEncoder.created[0]
        self.assertEqual(len(result), 2)
        self.assertEqual(service.active_device, "cpu")
        self.assertIn("cuda", created.model.moves)
        self.assertEqual(created.model.moves[-1], "cpu")
        self.assertEqual(cache_clear_calls["count"], 1)

    def test_swap_disabled_keeps_model_on_accelerator(self):
        cache_clear_calls = {"count": 0}

        def _fake_empty_cache():
            cache_clear_calls["count"] += 1

        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "BAAI/bge-reranker-v2-m3", "RERANKER_SWAP_TO_RAM": "0"},
            clear=False,
        ):
            with patch.object(reranker_module, "CrossEncoder", _FakeCrossEncoder):
                with patch.object(reranker_module, "_detect_preferred_device", return_value="cuda"):
                    with patch.object(reranker_module.torch.cuda, "is_available", return_value=True):
                        with patch.object(reranker_module.torch.cuda, "empty_cache", side_effect=_fake_empty_cache):
                            service = reranker_module.ZeRankerService()
                            asyncio.run(service.rerank_documents("query", ["doc-a", "doc-b"], top_k=2))

        created = _FakeCrossEncoder.created[0]
        self.assertEqual(service.active_device, "cuda")
        self.assertNotIn("cpu", created.model.moves)
        self.assertEqual(cache_clear_calls["count"], 0)

    def test_no_padding_token_falls_back_to_batch_size_one(self):
        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "Qwen/Qwen3-Reranker-0.6B", "RERANKER_SWAP_TO_RAM": "false"},
            clear=False,
        ):
            with patch.object(reranker_module, "CrossEncoder", _FakeCrossEncoderNoPad):
                with patch.object(reranker_module, "_detect_preferred_device", return_value="cpu"):
                    service = reranker_module.ZeRankerService()
                    asyncio.run(service.rerank_documents("query", ["doc-a", "doc-b"], top_k=2))

        created = _FakeCrossEncoder.created[0]
        # Skip warmup call; inspect actual rerank call.
        _, kwargs = created.predict_calls[-1]
        self.assertEqual(kwargs.get("batch_size"), 1)

    def test_runtime_padding_error_retries_with_batch_size_one(self):
        with patch.dict(
            os.environ,
            {"RERANKER_MODEL": "Qwen/Qwen3-Reranker-0.6B", "RERANKER_SWAP_TO_RAM": "false"},
            clear=False,
        ):
            with patch.object(reranker_module, "CrossEncoder", _FakeCrossEncoderBatchPaddingError):
                with patch.object(reranker_module, "_detect_preferred_device", return_value="cpu"):
                    service = reranker_module.ZeRankerService()
                    result = asyncio.run(
                        service.rerank_documents("query", ["doc-a", "doc-b", "doc-c"], top_k=2)
                    )

        created = _FakeCrossEncoder.created[0]
        self.assertEqual(len(created.predict_calls), 3)  # warmup + first try + retry
        _, first_kwargs = created.predict_calls[1]
        _, retry_kwargs = created.predict_calls[2]
        self.assertNotEqual(first_kwargs.get("batch_size"), 1)
        self.assertEqual(retry_kwargs.get("batch_size"), 1)
        self.assertEqual(service._predict_kwargs.get("batch_size"), 1)
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
