import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type {
    FileContentAsyncState,
    FileContentState,
    FilesState,
    ParentChunkContent,
    SaveNotification,
} from "../../../types";
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

function getReadableErrorMessage(error: unknown, fallback: string): string {
    if (error instanceof Error && error.message.trim()) return error.message.trim();
    if (typeof error === "string" && error.trim()) return error.trim();
    if (error && typeof error === "object") {
        const detail = (error as { detail?: unknown }).detail;
        if (typeof detail === "string" && detail.trim()) return detail.trim();
    }
    return fallback;
}

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
    const [savingFileIds, setSavingFileIds] = useState<Set<string>>(new Set());
    const [optimisticViewByFileId, setOptimisticViewByFileId] = useState<Record<string, string>>({});
    const [saveNotifications, setSaveNotifications] = useState<SaveNotification[]>([]);
    const [saveError, setSaveError] = useState<string | null>(null);
    const notificationTimersRef = useRef<Record<string, number>>({});

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

    const clearNotificationTimer = useCallback((fileId: string) => {
        const timerId = notificationTimersRef.current[fileId];
        if (timerId) {
            window.clearTimeout(timerId);
            delete notificationTimersRef.current[fileId];
        }
    }, []);

    const dismissSaveNotification = useCallback((fileId: string) => {
        clearNotificationTimer(fileId);
        setSaveNotifications((prev) => prev.filter((entry) => entry.fileId !== fileId));
    }, [clearNotificationTimer]);

    const upsertSaveNotification = useCallback(
        (params: {
            fileId: string;
            fileName: string;
            status: SaveNotification["status"];
            message: string;
            autoHideMs?: number;
        }) => {
            const { fileId, fileName, status, message, autoHideMs } = params;
            clearNotificationTimer(fileId);
            setSaveNotifications((prev) => {
                const next = prev.filter((entry) => entry.fileId !== fileId);
                next.push({
                    id: `save-${fileId}`,
                    fileId,
                    fileName,
                    status,
                    message,
                    createdAt: Date.now(),
                });
                return next;
            });
            if (autoHideMs && autoHideMs > 0) {
                notificationTimersRef.current[fileId] = window.setTimeout(() => {
                    setSaveNotifications((prev) => prev.filter((entry) => entry.fileId !== fileId));
                    delete notificationTimersRef.current[fileId];
                }, autoHideMs);
            }
        },
        [clearNotificationTimer]
    );

    useEffect(() => () => {
        const timers = Object.values(notificationTimersRef.current);
        timers.forEach((timerId) => window.clearTimeout(timerId));
        notificationTimersRef.current = {};
    }, []);

    const editingDocumentContent = useMemo(() => {
        if (!editingFileId) return "";
        return editingDraftByFileId[editingFileId] ?? getEditorBaselineContent(editingFileId);
    }, [editingDraftByFileId, editingFileId, getEditorBaselineContent]);

    const isEditingActiveDocument = Boolean(activeTab && editingFileId && activeTab === editingFileId);
    const isSavingActiveDocument = Boolean(activeTab && savingFileIds.has(activeTab));
    const isFileSaving = useCallback(
        (fileId: string | null) => Boolean(fileId && savingFileIds.has(fileId)),
        [savingFileIds]
    );
    const getDocumentViewContent = useCallback(
        (fileId: string | null) => {
            if (!fileId) return "";
            return optimisticViewByFileId[fileId] ?? getEditorBaselineContent(fileId);
        },
        [getEditorBaselineContent, optimisticViewByFileId]
    );

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
        if (savingFileIds.has(activeTab)) {
            setSaveError("This file is currently saving in the background. Wait until it finishes before editing again.");
            return;
        }
        const fullContent = getEditorBaselineContent(activeTab);
        setEditingFileId(activeTab);
        setEditingDraftByFileId((prev) => ({ ...prev, [activeTab]: prev[activeTab] ?? fullContent }));
        setSaveError(null);
    }, [activeTab, activeTabData?.chunks.length, getEditorBaselineContent, savingFileIds]);

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
        if (!activeTab || !editingFileId || activeTab !== editingFileId || savingGuard) return false;
        if (savingFileIds.has(activeTab)) {
            setSaveError("This file is already saving in the background.");
            return false;
        }
        const fileName = getFileNameById(activeTab);
        if (!fileName) { setSaveError("Missing file name for this tab. Please refresh."); return false; }

        // Build a normalized baseline from chunk content so diff math is stable.
        const state = getContentStateById(activeTab);
        const normalizedChunks = state.chunks.map((chunk) => ({
            ...chunk,
            content: normalizeMarkdownForEditor(chunk.content),
        }));
        const { fullText: original, ranges } = buildChunkRanges(normalizedChunks);

        const draftSnapshot = editingDraftByFileId[activeTab] ?? getEditorBaselineContent(activeTab);
        const normalizedDraft = normalizeMarkdownForEditor(draftSnapshot);
        if (!normalizedDraft.trim()) { setSaveError("Content cannot be empty."); return false; }
        if (!hasMeaningfulEditorChange(original, normalizedDraft)) { return false; }

        setSavingFileIds((prev) => {
            const next = new Set(prev);
            next.add(activeTab);
            return next;
        });
        setOptimisticViewByFileId((prev) => ({ ...prev, [activeTab]: draftSnapshot }));
        setEditingFileId((prev) => (prev === activeTab ? null : prev));
        setEditingDraftByFileId((prev) => {
            const next = { ...prev };
            delete next[activeTab];
            return next;
        });
        setSaveError(null);
        upsertSaveNotification({
            fileId: activeTab,
            fileName,
            status: "saving",
            message: `Saving "${fileName}"...`,
        });
        try {
            const shouldForceFullFileUpdate =
                containsRawHtmlMarkup(original) ||
                containsRawHtmlMarkup(draftSnapshot);

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
            clearDraftForFile(activeTab);
            setOptimisticViewByFileId((prev) => {
                const next = { ...prev };
                delete next[activeTab];
                return next;
            });
            upsertSaveNotification({
                fileId: activeTab,
                fileName,
                status: "saved",
                message: `Saved "${fileName}".`,
                autoHideMs: 2500,
            });
            return true;
        } catch (error) {
            const detail = getReadableErrorMessage(error, "Failed to save document changes.");
            setOptimisticViewByFileId((prev) => {
                const next = { ...prev };
                delete next[activeTab];
                return next;
            });
            setEditingDraftByFileId((prev) => ({ ...prev, [activeTab]: draftSnapshot }));
            setEditingFileId((prev) => (prev === null || prev === activeTab ? activeTab : prev));
            setSaveError(detail);
            upsertSaveNotification({
                fileId: activeTab,
                fileName,
                status: "failed",
                message: `Failed to save "${fileName}": ${detail}`,
            });
            return false;
        } finally {
            setSavingFileIds((prev) => {
                const next = new Set(prev);
                next.delete(activeTab);
                return next;
            });
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
        savingFileIds,
        savingGuard,
        setFilesState,
        upsertSaveNotification,
    ]);

    return {
        editingFileId,
        setEditingFileId,
        editingDraftByFileId,
        setEditingDraftByFileId,
        savingFileIds,
        saveError,
        setSaveError,
        saveNotifications,
        dismissSaveNotification,
        editingDocumentContent,
        getDocumentViewContent,
        isFileSaving,
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
