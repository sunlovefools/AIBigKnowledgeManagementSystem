// The main orchestrator hook used by components
// Contain of all the facade hooks related to document file management, editing, and agent interactions.

import { useCallback, useRef } from "react";
import { deleteKnowledgeFile, createBlankFile, renameKnowledgeFile, getAxiosErrorDetail, type DeleteFileResponse, type RenameFileResponse } from "./api/documentsApi";
import { removeFileFromState, closeTabState, openTabState } from "./state/transitions";
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

    const createNewBlankFile = useCallback(
        async (fileName: string): Promise<CreateFileResult> => {
            if (!fileName.trim()) return { ok: false, error: "File name must not be empty." };
            try {
                const result = await createBlankFile(fileName.trim());

                // ── Inject the new file directly into local state ──────────────
                // This avoids a fetchFiles() DB round-trip for the sidebar refresh
                // and a loadFileChunks() round-trip for the editor open.
                // The real parentId from the backend is used so that
                // saveEditingActiveDocument can map edits to chunks correctly —
                // a fake parentId would cause fast_updates / boundary_rechunk to
                // fail when they look it up in the DB.
                const syntheticChunk = {
                    parentId: result.parentId,
                    content: result.content,
                    size: result.content.length,
                    pageNumbers: [] as number[],
                };
                fileDomain.setFilesState((prev) => ({
                    ...prev,
                    byId: {
                        ...prev.byId,
                        [result.fileId]: {
                            fileId: result.fileId,
                            fileName: result.fileName,
                            previewTexts: result.content.slice(0, 240),
                            contentState: {
                                chunks: [syntheticChunk],
                                hasMore: false,
                                nextCursor: null,
                            },
                        },
                    },
                    sidebarFileIds: [...prev.sidebarFileIds, result.fileId],
                }));
                fileDomain.setChunkAsyncByFileId((prev) => ({
                    ...prev,
                    [result.fileId]: { isLoading: false, isInitialized: true, error: null },
                }));

                return { ok: true, fileId: result.fileId, initialContent: result.content };
            } catch (error) {
                const detail = getAxiosErrorDetail(error);
                return { ok: false, error: detail ?? "Failed to create file." };
            }
        },
        [fileDomain]
    );

    const renameFile = useCallback(
        async (fileId: string, newFileName: string): Promise<RenameFileResult> => {
            if (!fileId) return { ok: false, error: "Missing file ID." };
            if (!newFileName.trim()) return { ok: false, error: "File name must not be empty." };
            try {
                const result = await renameKnowledgeFile(fileId, newFileName.trim());
                // Refresh sidebar to reflect the updated name everywhere
                await fileDomain.fetchFiles();
                return { ok: true, data: result };
            } catch (error) {
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
        saveEditingActiveDocument: editingDomain.saveEditingActiveDocument,
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
