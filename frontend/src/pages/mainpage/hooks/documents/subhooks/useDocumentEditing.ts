import { useCallback, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import type { FileContentAsyncState, FileContentState, FilesState } from "../../../types";
import {
    submitSaveJob,
    type SubmitSaveJobPayload,
} from "../api/documentsApi";
import { patchFileContentOptimistically } from "../state/transitions";
import { buildChunkRanges } from "../utils/chunkText";
import {
    collectBoundaryTouchedParentIds,
    computeSingleReplaceEdit,
    containsRawHtmlMarkup,
    findTouchedRangesForEdit,
    hasMeaningfulEditorChange,
} from "../utils/editText";
import { createEmptyContentAsyncState } from "../state/factories";
import { normalizeMarkdownForEditor } from "../../../utils/markdownEditor";

export type PendingSaveJobRegistration = {
    jobId: string;
    fileId: string;
    fileName: string;
    previousFileName: string;
    submittedContent: string;
    previousContentState: FileContentState;
};

type UseDocumentEditingParams = {
    activeTab: string | null;
    activeTabData: FileContentState | null;
    savingGuard: string | null;
    setFilesState: Dispatch<SetStateAction<FilesState>>;
    setChunkAsyncByFileId: Dispatch<SetStateAction<Record<string, FileContentAsyncState>>>;
    getFileNameById: (fileId: string) => string;
    getContentStateById: (fileId: string) => FileContentState;
    getEditorBaselineContent: (fileId: string | null) => string;
    clearAgentStateForFile: (fileId: string) => void;
    registerPendingSaveJob: (job: PendingSaveJobRegistration) => void;
};

async function sha256Hex(text: string): Promise<string> {
    const encoded = new TextEncoder().encode(text);
    const digest = await crypto.subtle.digest("SHA-256", encoded);
    return Array.from(new Uint8Array(digest))
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
}

// Manages local edit sessions and persists changes using the most efficient backend path.
export function useDocumentEditing({
    activeTab,
    activeTabData,
    savingGuard,
    setFilesState,
    setChunkAsyncByFileId,
    getFileNameById,
    getContentStateById,
    getEditorBaselineContent,
    clearAgentStateForFile,
    registerPendingSaveJob,
}: UseDocumentEditingParams) {
    const [editingFileId, setEditingFileId] = useState<string | null>(null);
    const [editingDraftByFileId, setEditingDraftByFileId] = useState<Record<string, string>>({}); // Store the draft content for each file being edited
    const [savingFileId, setSavingFileId] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);

    const editingDocumentContent = useMemo(() => {
        if (!editingFileId) return "";
        return editingDraftByFileId[editingFileId] ?? getEditorBaselineContent(editingFileId);
    }, [editingDraftByFileId, editingFileId, getEditorBaselineContent]);

    const isEditingActiveDocument = Boolean(activeTab && editingFileId && activeTab === editingFileId);
    const isSavingActiveDocument = Boolean(activeTab && savingFileId && activeTab === savingFileId);

    const isActiveDocumentDirty = useMemo(() => {
        if (!activeTab || !isEditingActiveDocument) return false;
        const original = getEditorBaselineContent(activeTab);
        return hasMeaningfulEditorChange(original, editingDraftByFileId[activeTab] ?? original);
    }, [activeTab, editingDraftByFileId, getEditorBaselineContent, isEditingActiveDocument]);

    const clearDraftForFile = useCallback((fileId: string) => {
        setEditingDraftByFileId((prev) => {
            const next = { ...prev };
            delete next[fileId];
            return next;
        });
    }, []);

    const confirmDiscardUnsavedChanges = useCallback(() => {
        if (!editingFileId) return true;
        const original = getEditorBaselineContent(editingFileId);
        const draft = editingDraftByFileId[editingFileId] ?? original;
        // Auto-close edit mode when there are no meaningful differences.
        if (!hasMeaningfulEditorChange(original, draft)) {
            clearAgentStateForFile(editingFileId);
            clearDraftForFile(editingFileId);
            setEditingFileId(null);
            return true;
        }
        const ok = window.confirm("You have unsaved changes. Discard them?");
        if (ok) {
            clearAgentStateForFile(editingFileId);
            clearDraftForFile(editingFileId);
            setEditingFileId(null);
        }
        return ok;
    }, [clearAgentStateForFile, clearDraftForFile, editingDraftByFileId, editingFileId, getEditorBaselineContent]);

    const startEditingActiveDocument = useCallback(() => {
        if (!activeTab || !activeTabData?.chunks.length) return;
        const fullContent = getEditorBaselineContent(activeTab);
        setEditingFileId(activeTab);
        setEditingDraftByFileId((prev) => ({ ...prev, [activeTab]: prev[activeTab] ?? fullContent }));
        setSaveError(null);
    }, [activeTab, activeTabData?.chunks.length, getEditorBaselineContent]);

    const setActiveEditingDocumentContent = useCallback(
        (nextContent: string) => {
            if (!editingFileId) return;
            setEditingDraftByFileId((prev) => ({ ...prev, [editingFileId]: nextContent }));
        },
        [editingFileId]
    );

    const cancelEditingActiveDocument = useCallback(() => {
        if (editingFileId) {
            clearAgentStateForFile(editingFileId);
            clearDraftForFile(editingFileId);
        }
        setEditingFileId(null);
        setSaveError(null);
    }, [clearAgentStateForFile, clearDraftForFile, editingFileId]);

    const saveEditingActiveDocument = useCallback(async (options?: { newFileName?: string }) => {
        if (!activeTab || !editingFileId || activeTab !== editingFileId || savingFileId || savingGuard) return false;
        const fileName = getFileNameById(activeTab);
        if (!fileName) { setSaveError("Missing file name for this tab. Please refresh."); return false; }
        const nextFileName = options?.newFileName?.trim() || fileName;

        // Build a normalized baseline from chunk content so diff math is stable.
        const state = getContentStateById(activeTab);
        const normalizedChunks = state.chunks.map((chunk) => ({
            ...chunk,
            content: normalizeMarkdownForEditor(chunk.content),
        }));
        const { fullText: original, ranges } = buildChunkRanges(normalizedChunks);

        const draft = editingDraftByFileId[activeTab] ?? getEditorBaselineContent(activeTab);
        const normalizedDraft = normalizeMarkdownForEditor(draft);
        if (!normalizedDraft.trim()) { setSaveError("Content cannot be empty."); return false; }
        if (!hasMeaningfulEditorChange(original, normalizedDraft)) { return false; }

        setSavingFileId(activeTab);
        setSaveError(null);
        try {
            const shouldForceFullFileUpdate =
                containsRawHtmlMarkup(original) ||
                containsRawHtmlMarkup(draft);
            const jobPayload: SubmitSaveJobPayload = {
                fileId: activeTab,
                fileName,
                content: normalizedDraft,
                mode: "full_file",
                expectedContentHash: await sha256Hex(original),
                ...(nextFileName !== fileName ? { newFileName: nextFileName } : {}),
            };

            // Raw HTML can invalidate chunk-boundary assumptions, so fall back to full save.
            if (!shouldForceFullFileUpdate) {
                const editPart = computeSingleReplaceEdit(original, normalizedDraft);
                if (editPart) {
                    const touchedRanges = findTouchedRangesForEdit(ranges, editPart);
                    const boundaryTouchedParentIds = collectBoundaryTouchedParentIds(
                        ranges,
                        editPart,
                        original.length
                    );
                    const touchedParentIds = Array.from(new Set(touchedRanges.map((range) => range.parentId)));
                    const shouldUseBoundaryRechunk =
                        boundaryTouchedParentIds.length > 0 || touchedParentIds.length > 1;
                    const boundaryParentsForRequest =
                        boundaryTouchedParentIds.length > 0
                            ? boundaryTouchedParentIds
                            : touchedParentIds;

                    // Cross-boundary edits are rechunked server-side from full document text.
                    if (shouldUseBoundaryRechunk) {
                        jobPayload.mode = "boundary_rechunk";
                        jobPayload.touchedParentIds = boundaryParentsForRequest;
                    } else if (touchedRanges.length > 0) {
                        // Single-region edits can be mapped to touched chunks for fast updates.
                        const firstTouchedChunk = touchedRanges[0];
                        const lastTouchedChunk = touchedRanges[touchedRanges.length - 1];
                        const draftTouchedEnd = normalizedDraft.length - (original.length - lastTouchedChunk.end);
                        const nextWindow = normalizedDraft.slice(firstTouchedChunk.start, draftTouchedEnd);

                        const updates: Array<{ parentId: string; content: string }> = [];
                        let cursor = 0;
                        let canBatchUpdate = true;
                        for (let i = 0; i < touchedRanges.length; i += 1) {
                            const range = touchedRanges[i];
                            const originalLen = range.end - range.start;
                            const isLast = i === touchedRanges.length - 1;
                            const nextCursor = isLast ? nextWindow.length : Math.min(nextWindow.length, cursor + originalLen);
                            const segment = nextWindow.slice(cursor, nextCursor);
                            if (!segment.trim()) {
                                canBatchUpdate = false;
                                break;
                            }
                            updates.push({ parentId: range.parentId, content: segment });
                            cursor = nextCursor;
                        }

                        if (canBatchUpdate) {
                            jobPayload.mode = "fast_updates";
                            jobPayload.updates = updates;
                        } else {
                            // Empty segment or ambiguous mapping: use full-file fallback.
                            jobPayload.mode = "full_file";
                        }
                    } else {
                        // No clear touched ranges: use conservative full-file save.
                        jobPayload.mode = "full_file";
                    }
                } else {
                    // Could not compute a single replace window: use full-file save.
                    jobPayload.mode = "full_file";
                }
            }

            const accepted = await submitSaveJob(jobPayload);
            setFilesState((prev) =>
                patchFileContentOptimistically(
                    prev,
                    activeTab,
                    nextFileName,
                    normalizedDraft,
                    `pending-save-${accepted.jobId}`,
                )
            );
            setChunkAsyncByFileId((prev) => ({
                ...prev,
                [activeTab]: {
                    ...(prev[activeTab] ?? createEmptyContentAsyncState()),
                    isLoading: false,
                    isInitialized: true,
                    error: null,
                },
            }));
            registerPendingSaveJob({
                jobId: accepted.jobId,
                fileId: activeTab,
                fileName: nextFileName,
                previousFileName: fileName,
                submittedContent: normalizedDraft,
                previousContentState: state,
            });
            clearAgentStateForFile(activeTab);
            setEditingFileId(null);
            clearDraftForFile(activeTab);
            return true;
        } catch {
            setSaveError("Failed to save document changes. Please try again.");
            return false;
        } finally {
            setSavingFileId(null);
        }
    }, [
        activeTab,
        clearAgentStateForFile,
        clearDraftForFile,
        editingDraftByFileId,
        editingFileId,
        getContentStateById,
        getEditorBaselineContent,
        getFileNameById,
        registerPendingSaveJob,
        savingFileId,
        savingGuard,
        setChunkAsyncByFileId,
        setFilesState,
    ]);

    return {
        editingFileId,
        setEditingFileId,
        editingDraftByFileId,
        setEditingDraftByFileId,
        savingFileId,
        saveError,
        setSaveError,
        editingDocumentContent,
        isEditingActiveDocument,
        isSavingActiveDocument,
        isActiveDocumentDirty,
        confirmDiscardUnsavedChanges,
        startEditingActiveDocument,
        setActiveEditingDocumentContent,
        cancelEditingActiveDocument,
        saveEditingActiveDocument,
        clearDraftForFile,
    };
}
