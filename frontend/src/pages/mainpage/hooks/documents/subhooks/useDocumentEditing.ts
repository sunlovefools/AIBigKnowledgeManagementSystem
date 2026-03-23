import { useCallback, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import type { FileContentAsyncState, FileContentState, FilesState, ParentChunkContent } from "../../../types";
import {
    batchUpdateParentChunks,
    updateFileContent,
    type UpdateFileResponse,
} from "../api/documentsApi";
import { remapAfterFullFileUpdate } from "../state/transitions";
import { buildChunkRanges, buildPreviewText } from "../utils/chunkText";
import {
    collectBoundaryTouchedParentIds,
    computeSingleReplaceEdit,
    containsRawHtmlMarkup,
    findTouchedRangesForEdit,
    hasMeaningfulEditorChange,
} from "../utils/editText";
import { createEmptyContentAsyncState } from "../state/factories";
import { normalizeMarkdownForEditor } from "../../../utils/markdownEditor";

type UseDocumentEditingParams = {
    activeTab: string | null;
    activeTabData: FileContentState | null;
    savingGuard: string | null;
    setFilesState: Dispatch<SetStateAction<FilesState>>;
    setChunkAsyncByFileId: Dispatch<SetStateAction<Record<string, FileContentAsyncState>>>;
    fetchFiles: () => Promise<void>;
    loadFileChunks: (fileId: string, reset?: boolean) => Promise<ParentChunkContent[]>;
    getFileNameById: (fileId: string) => string;
    getContentStateById: (fileId: string) => FileContentState;
    getEditorBaselineContent: (fileId: string | null) => string;
    clearAgentStateForFile: (fileId: string) => void;
};

// Manages local edit sessions and persists changes using the most efficient backend path.
export function useDocumentEditing({
    activeTab,
    activeTabData,
    savingGuard,
    setFilesState,
    setChunkAsyncByFileId,
    fetchFiles,
    loadFileChunks,
    getFileNameById,
    getContentStateById,
    getEditorBaselineContent,
    clearAgentStateForFile,
}: UseDocumentEditingParams) {
    const [editingFileId, setEditingFileId] = useState<string | null>(null);
    const [editingDraftByFileId, setEditingDraftByFileId] = useState<Record<string, string>>({}); // Store the draft content for each file being edited
    const [savingFileId, setSavingFileId] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);

    // Applies backend full-file save response and remaps local IDs/chunk async metadata.
    const applyFullFileUpdate = useCallback((updated: UpdateFileResponse) => {
        const previousContentState = getContentStateById(updated.previousFileId);
        setFilesState((prev) =>
            remapAfterFullFileUpdate(prev, updated, previousContentState.chunks)
        );
        setChunkAsyncByFileId((prev) => {
            const next = { ...prev };
            const previousAsync = prev[updated.previousFileId] ?? createEmptyContentAsyncState();
            delete next[updated.previousFileId];
            next[updated.fileId] = {
                ...previousAsync,
                isLoading: false,
                isInitialized: true,
                error: null,
            };
            return next;
        });
    }, [getContentStateById, setChunkAsyncByFileId, setFilesState]);

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

    const saveEditingActiveDocument = useCallback(async () => {
        if (!activeTab || !editingFileId || activeTab !== editingFileId || savingFileId || savingGuard) return false;
        const fileName = getFileNameById(activeTab);
        if (!fileName) { setSaveError("Missing file name for this tab. Please refresh."); return false; }

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
                        const batchResp = await batchUpdateParentChunks({
                            fileId: activeTab,
                            fileName,
                            mode: "boundary_rechunk",
                            fullContent: normalizedDraft,
                            touchedParentIds: boundaryParentsForRequest,
                        });
                        if (batchResp.requiresReload) {
                            await fetchFiles();
                            await loadFileChunks(activeTab, true);
                        }
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
                            const batchResp = await batchUpdateParentChunks({
                                fileId: activeTab,
                                fileName,
                                mode: "fast_updates",
                                updates,
                            });

                            const updatedRows = batchResp.results ?? [];
                            if (!batchResp.requiresReload && updatedRows.length > 0) {
                                const replacementByPreviousId = new Map(
                                    updatedRows.map((row) => [row.previousParentId, row])
                                );

                                setFilesState((prev) => {
                                    const activeEntry = prev.byId[activeTab];
                                    if (!activeEntry) return prev;

                                    // Remap in-memory chunk IDs/content so UI updates immediately.
                                    const remappedChunks = activeEntry.contentState.chunks.map((chunk) => {
                                        const replacement = replacementByPreviousId.get(chunk.parentId);
                                        if (!replacement) return chunk;
                                        return {
                                            ...chunk,
                                            parentId: replacement.parentId,
                                            content: replacement.content,
                                            size: replacement.size,
                                        };
                                    });
                                    const dedupedChunks = Array.from(
                                        new Map(remappedChunks.map((chunk) => [chunk.parentId, chunk])).values()
                                    );
                                    const remappedContent = dedupedChunks.map((chunk) => chunk.content).join("\n\n");

                                    return {
                                        ...prev,
                                        byId: {
                                            ...prev.byId,
                                            [activeTab]: {
                                                ...activeEntry,
                                                previewTexts: buildPreviewText(remappedContent),
                                                contentState: {
                                                    ...activeEntry.contentState,
                                                    chunks: dedupedChunks,
                                                },
                                            },
                                        },
                                    };
                                });
                            }

                            await fetchFiles();
                            await loadFileChunks(activeTab, true);
                        } else {
                            // Empty segment or ambiguous mapping: use full-file fallback.
                            const updated = await updateFileContent(activeTab, fileName, normalizedDraft);
                            applyFullFileUpdate(updated);
                        }
                    } else {
                        // No clear touched ranges: use conservative full-file save.
                        const updated = await updateFileContent(activeTab, fileName, normalizedDraft);
                        applyFullFileUpdate(updated);
                    }
                } else {
                    // Could not compute a single replace window: use full-file save.
                    const updated = await updateFileContent(activeTab, fileName, normalizedDraft);
                    applyFullFileUpdate(updated);
                }
            } else {
                const updated = await updateFileContent(activeTab, fileName, normalizedDraft);
                applyFullFileUpdate(updated);
            }

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
        applyFullFileUpdate,
        clearAgentStateForFile,
        clearDraftForFile,
        editingDraftByFileId,
        editingFileId,
        fetchFiles,
        getContentStateById,
        getEditorBaselineContent,
        getFileNameById,
        loadFileChunks,
        savingFileId,
        savingGuard,
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
