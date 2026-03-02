"""Multi-file edit preview orchestration service with manual and auto selection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
from time import perf_counter
from typing import Any

from app.service.modification.llm_editor_service import LlmEditorService
from app.service.modification.reconstruction_service import ReconstructionService
from app.vectordb.vectordb import search_and_retrieve_context


logger = logging.getLogger("uvicorn.error")
BATCH_ANALYSIS_CONCURRENCY = 10


@dataclass(frozen=True)
class FileCandidate:
    file_name: str
    original_content: str


class MultiFileEditorService:
    """Batch edit preview orchestration that reuses single-file LLM editor service."""

    @staticmethod
    async def resolve_auto_candidates(candidates: list[FileCandidate]) -> list[FileCandidate]:
        if candidates:
            logger.info("[AI-BATCH] Using %d provided auto candidates", len(candidates))
            return candidates

        logger.info("[AI-BATCH] No auto candidates provided, fetching all preview files from DB")
        previews = await ReconstructionService.get_all_preview_files()
        resolved: list[FileCandidate] = []
        for item in previews:
            file_name = str(item.get("fileName") or "").strip()
            preview = str(item.get("preview") or "")
            if file_name and preview.strip():
                resolved.append(FileCandidate(file_name=file_name, original_content=preview))
        logger.info("[AI-BATCH] Resolved %d auto candidates from DB previews", len(resolved))
        return resolved

    @staticmethod
    def _extract_file_name_from_retrieved_doc(doc: dict[str, Any]) -> str | None:
        metadata = doc.get("metadata") if isinstance(doc, dict) else None
        if not isinstance(metadata, dict):
            return None

        file_name = metadata.get("file_name")
        if isinstance(file_name, str) and file_name.strip():
            return file_name.strip()

        file_metadata = metadata.get("file_metadata")
        if isinstance(file_metadata, dict):
            nested_name = file_metadata.get("file_name")
            if isinstance(nested_name, str) and nested_name.strip():
                return nested_name.strip()

        return None

    @staticmethod
    async def select_auto_candidates_via_retrieval(
        *,
        instruction: str,
        candidates: list[FileCandidate],
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []

        candidate_by_name = {item.file_name: item for item in candidates}
        top_k = max(10, len(candidates))
        logger.info("[AI-BATCH] Retrieval prefilter start: candidates=%d, top_k=%d", len(candidates), top_k)

        try:
            retrieved_docs = await search_and_retrieve_context(query=instruction, top_k=top_k)
        except Exception:
            logger.exception("[AI-BATCH] Retrieval prefilter failed; fallback to full scan candidates")
            return [
                {
                    "fileName": item.file_name,
                    "originalContent": item.original_content,
                    "score": None,
                    "reasons": ["auto fallback: retrieval failed"],
                }
                for item in candidates
            ]

        selected_names: list[str] = []
        seen: set[str] = set()
        for doc in retrieved_docs:
            file_name = MultiFileEditorService._extract_file_name_from_retrieved_doc(doc)
            if not file_name or file_name in seen:
                continue
            if file_name not in candidate_by_name:
                continue
            selected_names.append(file_name)
            seen.add(file_name)

        if not selected_names:
            logger.info("[AI-BATCH] Retrieval prefilter found 0 matched files; fallback to full scan")
            return [
                {
                    "fileName": item.file_name,
                    "originalContent": item.original_content,
                    "score": None,
                    "reasons": ["auto fallback: no retrieval matches"],
                }
                for item in candidates
            ]

        logger.info(
            "[AI-BATCH] Retrieval prefilter selected %d/%d files",
            len(selected_names),
            len(candidates),
        )
        return [
            {
                "fileName": file_name,
                "originalContent": candidate_by_name[file_name].original_content,
                "score": None,
                "reasons": ["retrieval prefilter match"],
            }
            for file_name in selected_names
        ]

    @staticmethod
    def select_auto_candidates_via_filename_hint(
        *,
        instruction: str,
        candidates: list[FileCandidate],
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []

        instruction_lower = (instruction or "").lower()
        if not instruction_lower.strip():
            return []

        matched_names: list[str] = []
        seen: set[str] = set()

        for candidate in candidates:
            full_name = candidate.file_name.strip()
            if not full_name:
                continue

            full_lower = full_name.lower()
            base_name = full_lower.rsplit(".", 1)[0] if "." in full_lower else full_lower

            has_full_match = full_lower in instruction_lower

            base_match_allowed = len(base_name) >= 4 and re.search(r"\.[a-z0-9]{1,6}$", full_lower) is not None
            has_base_match = base_match_allowed and base_name in instruction_lower

            if not has_full_match and not has_base_match:
                continue

            if full_name in seen:
                continue

            seen.add(full_name)
            matched_names.append(full_name)

        if not matched_names:
            logger.info("[AI-BATCH] Filename hint prefilter found 0 matches")
            return []

        logger.info(
            "[AI-BATCH] Filename hint prefilter selected %d/%d files",
            len(matched_names),
            len(candidates),
        )
        candidate_by_name = {item.file_name: item for item in candidates}
        return [
            {
                "fileName": file_name,
                "originalContent": candidate_by_name[file_name].original_content,
                "score": None,
                "reasons": ["explicit filename match"],
            }
            for file_name in matched_names
        ]

    @staticmethod
    async def generate_batch_edit_preview(
        *,
        instruction: str,
        selection_mode: str,
        targets: list[FileCandidate],
        auto_candidates: list[FileCandidate],
        max_files: int,
        min_score: float,
        active_file_name: str | None,
    ) -> dict:
        _ = (max_files, min_score, active_file_name)
        started_at = perf_counter()
        scan_candidates: list[dict]

        if selection_mode == "manual":
            scan_candidates = [
                {
                    "fileName": target.file_name,
                    "originalContent": target.original_content,
                    "score": 1.0,
                    "reasons": ["manual selection"],
                }
                for target in targets
            ]
        else:
            resolved_candidates = await MultiFileEditorService.resolve_auto_candidates(auto_candidates)
            scan_candidates = MultiFileEditorService.select_auto_candidates_via_filename_hint(
                instruction=instruction,
                candidates=resolved_candidates,
            )

            if scan_candidates:
                logger.info("[AI-BATCH] Auto selection path: explicit filename match")
            else:
                logger.info("[AI-BATCH] Auto selection path: retrieval prefilter")
                scan_candidates = await MultiFileEditorService.select_auto_candidates_via_retrieval(
                    instruction=instruction,
                    candidates=resolved_candidates,
                )

        logger.info(
            "[AI-BATCH] Start processing: mode=%s, candidates=%d, instruction_len=%d, concurrency=%d",
            selection_mode,
            len(scan_candidates),
            len(instruction or ""),
            BATCH_ANALYSIS_CONCURRENCY,
        )

        selected_files: list[dict] = []
        results: list[dict] = []

        semaphore = asyncio.Semaphore(BATCH_ANALYSIS_CONCURRENCY)

        async def _process_candidate(index: int, candidate: dict[str, Any]) -> tuple[dict | None, dict | None]:
            file_name = candidate["fileName"]
            original_content = candidate["originalContent"]

            async with semaphore:
                logger.info("[AI-BATCH] [%d/%d] Evaluating file: %s", index, len(scan_candidates), file_name)

                try:
                    preview = await LlmEditorService.generate_edit_preview(
                        file_name=file_name,
                        original_content=original_content,
                        instruction=instruction,
                    )

                    edited_content = preview["editedContent"]
                    is_relevant = bool(preview.get("isRelevant", False))
                    has_changes = edited_content.strip() != original_content.strip()

                    if not is_relevant or not has_changes:
                        logger.info(
                            "[AI-BATCH] [%d/%d] Skipped file: %s (relevant=%s, changed=%s)",
                            index,
                            len(scan_candidates),
                            file_name,
                            is_relevant,
                            has_changes,
                        )
                        return None, None

                    selected_item = {
                        "fileName": file_name,
                        "score": candidate.get("score"),
                        "reasons": ["ai relevance match"],
                    }
                    result_item = {
                        "fileName": file_name,
                        "ok": True,
                        "editedContent": edited_content,
                        "summary": preview["summary"],
                        "warnings": preview.get("warnings", []),
                    }
                    logger.info("[AI-BATCH] [%d/%d] Selected file: %s", index, len(scan_candidates), file_name)
                    return selected_item, result_item
                except Exception as exc:
                    logger.exception("[AI-BATCH] [%d/%d] Error processing file: %s", index, len(scan_candidates), file_name)
                    return None, {
                        "fileName": file_name,
                        "ok": False,
                        "error": str(exc),
                    }

        task_results = await asyncio.gather(
            *[_process_candidate(index, candidate) for index, candidate in enumerate(scan_candidates, start=1)]
        )

        for selected_item, result_item in task_results:
            if selected_item is not None:
                selected_files.append(selected_item)
            if result_item is not None:
                results.append(result_item)

        success_count = sum(1 for item in results if item.get("ok") is True)
        failed_count = len(results) - success_count
        elapsed_s = perf_counter() - started_at

        logger.info(
            "[AI-BATCH] Completed: selected=%d, success=%d, failed=%d, elapsed=%.2fs",
            len(selected_files),
            success_count,
            failed_count,
            elapsed_s,
        )

        return {
            "selectionMode": selection_mode,
            "selectedFiles": [
                {
                    "fileName": item["fileName"],
                    "score": item.get("score"),
                    "reasons": item.get("reasons", []),
                }
                for item in selected_files
            ],
            "results": results,
            "stats": {
                "total": len(results),
                "success": success_count,
                "failed": failed_count,
            },
        }
