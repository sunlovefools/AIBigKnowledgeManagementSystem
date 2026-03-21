// The main orchestrator hook used by components
// Contain of all the facade hooks related to document file management, editing, and agent interactions.

import { useCallback, useRef, useState } from "react";
import { deleteKnowledgeFile, createBlankFile, renameKnowledgeFile, getAxiosErrorDetail, type DeleteFileResponse, type RenameFileResponse } from "./api/documentsApi";
import { removeFileFromState, closeTabState, openTabState, swapTempFileId, patchFileName } from "./state/transitions";
import { useDocumentAgent } from "./subhooks/useDocumentAgent";
import { useDocumentEditing } from "./subhooks/useDocumentEditing";
import { useDocumentFiles } from "./subhooks/useDocumentFiles";

// Result contract returned by deleteFile so callers can render user-facing status.
type DeleteFileResult = {
    ok: boolean;
    data?: DeleteFileResponse;
    error?: string;
};

type CreateFileResult = {
    ok: boolean;
    fileId?: string;
    initialContent?: string;
    error?: string;
};

type RenameFileResult = {
    ok: boolean;
    data?: RenameFileResponse;
    error?: string;
};

// Deal with file and chunk state
export function useDocuments(isModificationPanelOpen: boolean) {
    const fileDomain = useDocumentFiles({ isModificationPanelOpen });

    // function to get the full content of a document by file ID
    const getFullDocumentContent = useCallback(
        (fileId: string | null) => {
            if (!fileId) return "";
            return fileDomain.getContentStateById(fileId).chunks.map((c) => c.content).join("\n\n");
        },
        [fileDomain]
    );

    // function to get the baseline content for the editor, which is used for diffing and agent proposals
    const getEditorBaselineContent = useCallback(
        (fileId: string | null) => {
            if (!fileId) return "";
            const original = getFullDocumentContent(fileId);
            if (!original) return "";
            return original;
        },
        [getFullDocumentContent]
    );

    // useDocumentEditing needs a callback to clear agent state, but the agent hook
    // is initialized afterwards. This ref breaks the setup cycle safely.
    const clearAgentStateForFileRef = useRef<(fileId: string) => void>(() => undefined);
    const editingDomain = useDocumentEditing({
        activeTab: fileDomain.activeTab,
        activeTabData: fileDomain.activeTabData,
        savingGuard: null,
        setFilesState: fileDomain.setFilesState,
        setChunkAsyncByFileId: fileDomain.setChunkAsyncByFileId,
        fetchFiles: fileDomain.fetchFiles,
        loadFileChunks: fileDomain.loadFileChunks,
        getFileNameById: fileDomain.getFileNameById,
        getContentStateById: fileDomain.getContentStateById,
        getEditorBaselineContent,
        clearAgentStateForFile: (fileId: string) => clearAgentStateForFileRef.current(fileId),
    });

    const agentDomain = useDocumentAgent({
        editingFileId: editingDomain.editingFileId,
        editingDraftByFileId: editingDomain.editingDraftByFileId,
        setEditingFileId: editingDomain.setEditingFileId,
        setEditingDraftByFileId: editingDomain.setEditingDraftByFileId,
        setSaveError: editingDomain.setSaveError,
        getContentStateById: fileDomain.getContentStateById,
        getEditorBaselineContent,
        loadFileChunks: fileDomain.loadFileChunks,
        setFilesState: fileDomain.setFilesState,
    });
    clearAgentStateForFileRef.current = agentDomain.clearAgentStateForFile;

    // Tab switches are guarded so users do not accidentally lose unsaved edits.
    const openDocumentTab = useCallback(
        async (fileId: string) => {
            if (!editingDomain.confirmDiscardUnsavedChanges()) return;
            fileDomain.setFilesState((prev) => openTabState(prev, fileId));
            await fileDomain.loadFileChunks(fileId, true);
        },
        [editingDomain, fileDomain]
    );

    // Like openDocumentTab, but immediately enters edit mode after chunks load.
    // Uses fileId directly to avoid stale-closure issues with activeTab/activeTabData.
    // When initialContent is supplied (e.g. right after a blank-file creation) the DB
    // load is skipped entirely — content is already in local state.
    const openDocumentTabAndEdit = useCallback(
        async (fileId: string, initialContent?: string) => {
            if (!editingDomain.confirmDiscardUnsavedChanges()) return;
            fileDomain.setFilesState((prev) => openTabState(prev, fileId));

            let editContent = initialContent;
            if (editContent === undefined) {
                // Normal open: load chunks from DB
                const chunks = await fileDomain.loadFileChunks(fileId, true);
                if (!chunks.length) return;
                editContent = chunks.map((c) => c.content).join("\n\n");
            }

            editingDomain.setEditingFileId(fileId);
            editingDomain.setEditingDraftByFileId((prev) => ({
                ...prev,
                [fileId]: prev[fileId] ?? editContent!,
            }));
            editingDomain.setSaveError(null);
        },
        [editingDomain, fileDomain]
    );

    const closeDocumentTab = useCallback((fileId: string) => {
        if (!editingDomain.confirmDiscardUnsavedChanges()) return;
        fileDomain.setFilesState((prev) => closeTabState(prev, fileId));
    }, [editingDomain, fileDomain]);

    const setActiveDocumentTab = useCallback(
        async (fileId: string) => {
            if (!editingDomain.confirmDiscardUnsavedChanges()) return;
            fileDomain.setFilesState((prev) => ({ ...prev, activeFileId: fileId }));
            const asyncState = fileDomain.getChunkAsyncById(fileId);
            if (!asyncState.isInitialized) await fileDomain.loadFileChunks(fileId, false);
        },
        [editingDomain, fileDomain]
    );

    const loadMoreActiveTab = useCallback(async () => {
        if (!fileDomain.activeTab) return;
        await fileDomain.loadFileChunks(fileDomain.activeTab, false);
    }, [fileDomain]);

    const handleRefreshDocuments = useCallback(async () => {
        if (!editingDomain.confirmDiscardUnsavedChanges()) return;
        await fileDomain.fetchFiles();
    }, [editingDomain, fileDomain]);

    const deleteFile = useCallback(
        async (fileId: string): Promise<DeleteFileResult> => {
            if (!fileId) return { ok: false, error: "Missing file ID." };
            if (fileDomain.deletingFileId) return { ok: false, error: "Another delete is already in progress." };

            // Clear local state first after a successful delete to keep UI consistent
            // even before any follow-up refreshes.
            fileDomain.setDeletingFileId(fileId);
            editingDomain.setSaveError(null);
            try {
                const deleted = await deleteKnowledgeFile(fileId);

                fileDomain.setFilesState((prev) => removeFileFromState(prev, fileId));
                fileDomain.setChunkAsyncByFileId((prev) => {
                    const next = { ...prev };
                    delete next[fileId];
                    return next;
                });
                editingDomain.clearDraftForFile(fileId);
                editingDomain.setEditingFileId((prev) => (prev === fileId ? null : prev));
                agentDomain.clearAgentStateForFile(fileId);

                return { ok: true, data: deleted };
            } catch (error) {
                const detail = getAxiosErrorDetail(error);
                return {
                    ok: false,
                    error: detail ?? "Failed to delete file from the knowledge base.",
                };
            } finally {
                fileDomain.setDeletingFileId(null);
            }
        },
        [agentDomain, editingDomain, fileDomain]
    );

    // Tracks fileIds that are still being written to the DB (temp IDs in flight).
    // Used to block save until the real ID is available.
    const [pendingCreationFileIds, setPendingCreationFileIds] = useState<Set<string>>(new Set());

    const createNewBlankFile = useCallback(
        async (fileName: string): Promise<CreateFileResult> => {
            if (!fileName.trim()) return { ok: false, error: "File name must not be empty." };

            // ── Step 1: Generate a client-side temp ID and inject state immediately ──
            // The user sees the editor open with zero network wait.
            const tempId = `tmp-${crypto.randomUUID()}`;
            const placeholderContent = `# ${fileName.trim()}\n\n(blank file — start writing here)`;
            const syntheticChunk = {
                parentId: tempId,           // will be swapped to real parentId on commit
                content: placeholderContent,
                size: placeholderContent.length,
                pageNumbers: [] as number[],
            };

            // Inject the optimistic file entry into sidebar + chunk state
            fileDomain.setFilesState((prev) => ({
                ...prev,
                byId: {
                    ...prev.byId,
                    [tempId]: {
                        fileId: tempId,
                        fileName: fileName.trim(),
                        previewTexts: placeholderContent.slice(0, 240),
                        contentState: { chunks: [syntheticChunk], hasMore: false, nextCursor: null },
                    },
                },
                sidebarFileIds: [...prev.sidebarFileIds, tempId],
            }));
            fileDomain.setChunkAsyncByFileId((prev) => ({
                ...prev,
                [tempId]: { isLoading: false, isInitialized: true, error: null },
            }));

            // Mark this tempId as pending so the save guard can block if needed
            setPendingCreationFileIds((prev) => new Set([...prev, tempId]));

            // ── Step 2: Fire the DB write in the background ────────────────────────
            // We do NOT await — the editor opens instantly while this runs.
            createBlankFile(fileName.trim()).then((result) => {
                // ── Step 3 (success): swap temp ID → real ID across all state ──────
                fileDomain.setFilesState((prev) =>
                    swapTempFileId(prev, tempId, result.fileId, result.parentId)
                );
                fileDomain.setChunkAsyncByFileId((prev) => {
                    const next = { ...prev };
                    next[result.fileId] = next[tempId] ?? { isLoading: false, isInitialized: true, error: null };
                    delete next[tempId];
                    return next;
                });
                // Swap editing draft so saves go to the real ID
                editingDomain.setEditingFileId((prev) => (prev === tempId ? result.fileId : prev));
                editingDomain.setEditingDraftByFileId((prev) => {
                    const next = { ...prev };
                    if (next[tempId] !== undefined) {
                        next[result.fileId] = next[tempId];
                        delete next[tempId];
                    }
                    return next;
                });
                setPendingCreationFileIds((prev) => {
                    const next = new Set(prev);
                    next.delete(tempId);
                    return next;
                });
                // ── Background chunk reload ───────────────────────────────────────
                // Replaces the synthetic chunk with the real parentId from the DB.
                // This runs while the user is typing, so UX is unaffected.
                // It ensures applyFullFileUpdate (triggered on save) always sees a
                // real parentId in state instead of a stale "local-xxx" fallback.
                void fileDomain.loadFileChunks(result.fileId, true);
            }).catch((error) => {
                // ── Step 3 (failure): rollback the optimistic state ───────────────
                fileDomain.setFilesState((prev) => removeFileFromState(prev, tempId));
                fileDomain.setChunkAsyncByFileId((prev) => {
                    const next = { ...prev };
                    delete next[tempId];
                    return next;
                });
                editingDomain.setEditingFileId((prev) => (prev === tempId ? null : prev));
                editingDomain.setEditingDraftByFileId((prev) => {
                    const next = { ...prev };
                    delete next[tempId];
                    return next;
                });
                setPendingCreationFileIds((prev) => {
                    const next = new Set(prev);
                    next.delete(tempId);
                    return next;
                });
                const detail = getAxiosErrorDetail(error);
                editingDomain.setSaveError(detail ?? "Failed to create file — please try again.");
            });

            // Return immediately with the temp ID so the caller can open the editor
            return { ok: true, fileId: tempId, initialContent: placeholderContent };
        },
        [editingDomain, fileDomain]
    );

    // Wraps saveEditingActiveDocument so that if the user somehow saves before the
    // background creation commits, we surface a clear message instead of a DB error.
    const saveEditingActiveDocumentGuarded = useCallback(async (): Promise<boolean> => {
        const tab = fileDomain.activeTab;
        if (tab && pendingCreationFileIds.has(tab)) {
            editingDomain.setSaveError("File is still being created — please wait a moment, then save again.");
            return false;
        }
        // Secondary guard: if local state still has a synthetic/fake parentId
        // (local-xxx or tmp-xxx), the background chunk reload hasn't finished.
        // Block the save to prevent a broken batch-update request.
        if (tab) {
            const chunks = fileDomain.getContentStateById(tab).chunks;
            const hasFakeId = chunks.some(
                (c) => c.parentId.startsWith("tmp-") || c.parentId.startsWith("local-")
            );
            if (hasFakeId) {
                editingDomain.setSaveError("File is still syncing — please wait a moment, then save again.");
                return false;
            }
        }
        return editingDomain.saveEditingActiveDocument();
    }, [editingDomain, fileDomain, pendingCreationFileIds]);

    const renameFile = useCallback(
        async (fileId: string, newFileName: string): Promise<RenameFileResult> => {
            if (!fileId) return { ok: false, error: "Missing file ID." };
            const trimmed = newFileName.trim();
            if (!trimmed) return { ok: false, error: "File name must not be empty." };

            // Snapshot the old name so we can rollback on failure
            const oldFileName = fileDomain.filesState.byId[fileId]?.fileName ?? "";

            // ── Step 1: Update local state immediately (zero wait) ─────────────
            fileDomain.setFilesState((prev) => patchFileName(prev, fileId, trimmed));

            // ── Step 2: Fire the DB write in the background ────────────────────
            try {
                const result = await renameKnowledgeFile(fileId, trimmed);
                return { ok: true, data: result };
            } catch (error) {
                // ── Rollback on failure: restore old name ──────────────────────
                fileDomain.setFilesState((prev) => patchFileName(prev, fileId, oldFileName));
                const detail = getAxiosErrorDetail(error);
                return { ok: false, error: detail ?? "Failed to rename file." };
            }
        },
        [fileDomain]
    );

    return {
        files: fileDomain.files,
        filesState: fileDomain.filesState,
        chunkAsyncByFileId: fileDomain.chunkAsyncByFileId,
        isLoadingFiles: fileDomain.isLoadingFiles,
        fileListError: fileDomain.fileListError,
        deletingFileId: fileDomain.deletingFileId,
        openTabs: fileDomain.openTabs,
        activeTab: fileDomain.activeTab,
        activeTabData: fileDomain.activeTabData,
        activeTabAsync: fileDomain.activeTabAsync,
        fetchFiles: fileDomain.fetchFiles,
        handleRefreshDocuments,
        invalidateDocumentCache: fileDomain.invalidateDocumentCache,
        deleteFile,
        createNewBlankFile,
        renameFile,
        openDocumentTab,
        openDocumentTabAndEdit,
        closeDocumentTab,
        setActiveDocumentTab,
        loadMoreActiveTab,
        saveError: editingDomain.saveError,
        editingDocumentContent: editingDomain.editingDocumentContent,
        isEditingActiveDocument: editingDomain.isEditingActiveDocument,
        isSavingActiveDocument: editingDomain.isSavingActiveDocument,
        isActiveDocumentDirty: editingDomain.isActiveDocumentDirty,
        startEditingActiveDocument: editingDomain.startEditingActiveDocument,
        setActiveEditingDocumentContent: editingDomain.setActiveEditingDocumentContent,
        cancelEditingActiveDocument: editingDomain.cancelEditingActiveDocument,
        saveEditingActiveDocument: saveEditingActiveDocumentGuarded,
        pendingCreationFileIds,
        getFileNameById: fileDomain.getFileNameById,
        getFileIdByName: fileDomain.getFileIdByName,
        isAgentGenerating: agentDomain.isAgentGenerating,
        agentProposals: agentDomain.agentProposals,
        agentAcceptedMap: agentDomain.agentAcceptedMap,
        agentRejectedIds: agentDomain.agentRejectedIds,
        agentError: agentDomain.agentError,
        agentIntention: agentDomain.agentIntention,
        requestAgentEditPreview: agentDomain.requestAgentEditPreview,
        requestSelectionEditPreview: agentDomain.requestSelectionEditPreview,
        acceptAgentProposal: agentDomain.acceptAgentProposal,
        rejectAgentProposal: agentDomain.rejectAgentProposal,
        clearAgentState: agentDomain.clearAgentState,
    };
}
