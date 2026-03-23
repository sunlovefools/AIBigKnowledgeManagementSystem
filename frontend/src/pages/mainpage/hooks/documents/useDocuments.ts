// The main orchestrator hook used by components
// Contain of all the facade hooks related to document file management, editing, and agent interactions.

import { useCallback, useRef } from "react";
import { deleteKnowledgeFile, getAxiosErrorDetail, type DeleteFileResponse } from "./api/documentsApi";
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

// Deal with file and chunk state
export function useDocuments(isModificationPanelOpen: boolean) {
    const fileDomain = useDocumentFiles({ isModificationPanelOpen });
    const { getContentStateById, loadFileChunks } = fileDomain;

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

    const ensureFileFullyLoaded = useCallback(
        async (fileId: string) => {
            if (!fileId) return;
            const state = getContentStateById(fileId);
            if (!state.chunks.length) {
                await loadFileChunks(fileId, true);
            }

            let safetyCounter = 0;
            let current = getContentStateById(fileId);
            while (current.hasMore && safetyCounter < 200) {
                await loadFileChunks(fileId, false);
                current = getContentStateById(fileId);
                safetyCounter += 1;
            }
        },
        [getContentStateById, loadFileChunks]
    );

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
        openDocumentTab,
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
        undoAgentProposal: agentDomain.undoAgentProposal,
        clearAgentState: agentDomain.clearAgentState,
        ensureFileFullyLoaded,
    };
}
