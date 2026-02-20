import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import type { FileTabState, ParentChunkContent, SidebarFileSummary } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");
const PAGE_SIZE = 7;

type UpdateParentChunkResponse = {
    parentId: string;
    previousParentId: string;
    fileName: string;
    content: string;
    size: number;
    chunks: number;
};

type UpdateFileResponse = {
    fileId: string;
    previousFileId: string;
    fileName: string;
    content: string;
    size: number;
    parentChunks: number;
    chunks: number;
};

function buildPreviewText(content: string): string {
    return content.replace(/\s+/g, " ").trim().slice(0, 160);
}

function createEmptyTabState(): FileTabState {
    return {
        chunks: [],
        hasMore: true,
        nextCursor: null,
        isLoading: false,
        isInitialized: false,
        error: null,
    };
}

// Custom hook to manage sidebar files and tabbed full-view state.
export function useDocuments(isModificationPanelOpen: boolean) {
    const [files, setFiles] = useState<SidebarFileSummary[]>([]);
    const [isLoadingFiles, setIsLoadingFiles] = useState(false);
    const [fileListError, setFileListError] = useState<string | null>(null);
    const [isDocsCached, setIsDocsCached] = useState(false);
    const [openTabs, setOpenTabs] = useState<string[]>([]);
    const [activeTab, setActiveTab] = useState<string | null>(null);
    const [tabStates, setTabStates] = useState<Record<string, FileTabState>>({});
    const [selectedParentByTab, setSelectedParentByTab] = useState<Record<string, string | null>>({});
    const [editingParentId, setEditingParentId] = useState<string | null>(null);
    const [editingDraftByParentId, setEditingDraftByParentId] = useState<Record<string, string>>({});
    const [savingParentId, setSavingParentId] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [editingFileName, setEditingFileName] = useState<string | null>(null);
    const [editingDraftByFileName, setEditingDraftByFileName] = useState<Record<string, string>>({});
    const [savingFileName, setSavingFileName] = useState<string | null>(null);

    const getFileIdByName = useCallback(
        (fileName: string) => files.find((item) => item.fileName === fileName)?.fileId ?? null,
        [files]
    );

    const fetchFiles = useCallback(async () => {
        setIsLoadingFiles(true);
        setFileListError(null);
        try {
            const response = await axios.get(`${API_BASE}/api/retrieve/all-preview-files`);
            const incomingFiles = (response.data.files ?? []) as SidebarFileSummary[];
            setFiles(incomingFiles);
            setIsDocsCached(true);

            const validNames = new Set(incomingFiles.map((item) => item.fileName));

            setOpenTabs((previousTabs) => previousTabs.filter((fileName) => validNames.has(fileName)));
            setSelectedParentByTab((previousSelections) => {
                const nextSelections: Record<string, string | null> = {};
                Object.entries(previousSelections).forEach(([fileName, parentId]) => {
                    if (validNames.has(fileName)) {
                        nextSelections[fileName] = parentId;
                    }
                });
                return nextSelections;
            });
            setTabStates((previousStates) => {
                const nextStates: Record<string, FileTabState> = {};
                Object.entries(previousStates).forEach(([fileName, state]) => {
                    if (validNames.has(fileName)) {
                        nextStates[fileName] = state;
                    }
                });
                return nextStates;
            });
            setActiveTab((previousActiveTab) =>
                previousActiveTab && validNames.has(previousActiveTab) ? previousActiveTab : null
            );
        } catch (error) {
            console.error("Error fetching documents:", error);
            setFileListError("Failed to load files from vector database.");
        } finally {
            setIsLoadingFiles(false);
        }
    }, []);

    useEffect(() => {
        if (isModificationPanelOpen && !isDocsCached) {
            void fetchFiles();
        }
    }, [isDocsCached, isModificationPanelOpen, fetchFiles]);

    const loadFileChunks = useCallback(
        async (fileName: string, reset = false) => {
            const fileId = getFileIdByName(fileName);
            if (!fileId) {
                setTabStates((previousStates) => {
                    const state = previousStates[fileName] ?? createEmptyTabState();
                    return {
                        ...previousStates,
                        [fileName]: {
                            ...state,
                            isLoading: false,
                            isInitialized: true,
                            error: `Missing file ID for ${fileName}. Please refresh documents.`,
                        },
                    };
                });
                return;
            }

            const currentState = tabStates[fileName] ?? createEmptyTabState();

            if (currentState.isLoading) {
                return;
            }

            if (!reset && currentState.isInitialized && !currentState.hasMore) {
                return;
            }

            setTabStates((previousStates) => {
                const state = previousStates[fileName] ?? createEmptyTabState();
                return {
                    ...previousStates,
                    [fileName]: {
                        ...state,
                        ...(reset
                            ? {
                                  chunks: [],
                                  nextCursor: null,
                                  hasMore: true,
                                  isInitialized: false,
                              }
                            : {}),
                        isLoading: true,
                        error: null,
                    },
                };
            });

            const cursor = reset ? null : currentState.nextCursor;

            try {
                const response = await axios.get(`${API_BASE}/api/retrieve/file-chunks`, {
                    params: {
                        fileId,
                        limit: PAGE_SIZE,
                        ...(cursor ? { cursor } : {}),
                    },
                });

                const incomingChunks = (response.data.chunks ?? []) as ParentChunkContent[];

                setTabStates((previousStates) => {
                    const state = previousStates[fileName] ?? createEmptyTabState();
                    const mergedChunks = reset ? incomingChunks : [...state.chunks, ...incomingChunks];
                    const dedupedChunks = Array.from(
                        new Map(mergedChunks.map((chunk) => [chunk.parentId, chunk])).values()
                    );

                    return {
                        ...previousStates,
                        [fileName]: {
                            ...state,
                            chunks: dedupedChunks,
                            hasMore: Boolean(response.data.hasMore),
                            nextCursor: response.data.nextCursor ?? null,
                            isLoading: false,
                            isInitialized: true,
                            error: null,
                        },
                    };
                });

                setSelectedParentByTab((previousSelections) => {
                    const previousSelected = previousSelections[fileName] ?? null;
                    const containsPrevious = incomingChunks.some((chunk) => chunk.parentId === previousSelected);
                    const nextSelected =
                        previousSelected && (containsPrevious || !reset)
                            ? previousSelected
                            : (incomingChunks[0]?.parentId ?? previousSelected ?? null);

                    return {
                        ...previousSelections,
                        [fileName]: nextSelected,
                    };
                });
            } catch (error) {
                console.error(`Error loading chunks for ${fileName}:`, error);
                setTabStates((previousStates) => {
                    const state = previousStates[fileName] ?? createEmptyTabState();
                    return {
                        ...previousStates,
                        [fileName]: {
                            ...state,
                            isLoading: false,
                            isInitialized: true,
                            error: `Failed to load document content for ${fileName}.`,
                        },
                    };
                });
            }
        },
        [getFileIdByName, tabStates]
    );

    const getChunkContentByParentId = useCallback(
        (parentId: string | null) => {
            if (!parentId) {
                return null;
            }

            for (const tabState of Object.values(tabStates)) {
                const matchedChunk = tabState.chunks.find((chunk) => chunk.parentId === parentId);
                if (matchedChunk) {
                    return matchedChunk.content;
                }
            }

            return null;
        },
        [tabStates]
    );

    const getFullDocumentContentByFileName = useCallback(
        (fileName: string | null) => {
            if (!fileName) {
                return "";
            }

            const fileState = tabStates[fileName] ?? createEmptyTabState();
            return fileState.chunks
                .map((chunk) => chunk.content)
                .join("\n\n")
                .trim();
        },
        [tabStates]
    );

    const clearEditingState = useCallback(() => {
        setEditingParentId((previousEditingParentId) => {
            if (!previousEditingParentId) {
                return null;
            }

            setEditingDraftByParentId((previousDrafts) => {
                if (!(previousEditingParentId in previousDrafts)) {
                    return previousDrafts;
                }

                const { [previousEditingParentId]: _removedDraft, ...nextDrafts } = previousDrafts;
                return nextDrafts;
            });

            return null;
        });

        setEditingFileName((previousEditingFileName) => {
            if (!previousEditingFileName) {
                return null;
            }

            setEditingDraftByFileName((previousDrafts) => {
                if (!(previousEditingFileName in previousDrafts)) {
                    return previousDrafts;
                }

                const { [previousEditingFileName]: _removedDraft, ...nextDrafts } = previousDrafts;
                return nextDrafts;
            });

            return null;
        });
        setSaveError(null);
    }, []);

    const confirmDiscardUnsavedChanges = useCallback(() => {
        if (!editingParentId && !editingFileName) {
            return true;
        }

        let hasUnsavedChanges = false;

        if (editingParentId) {
            const originalContent = getChunkContentByParentId(editingParentId) ?? "";
            const draftContent = editingDraftByParentId[editingParentId] ?? originalContent;
            hasUnsavedChanges = draftContent !== originalContent;
        }

        if (!hasUnsavedChanges && editingFileName) {
            const originalContent = getFullDocumentContentByFileName(editingFileName);
            const draftContent = editingDraftByFileName[editingFileName] ?? originalContent;
            hasUnsavedChanges = draftContent !== originalContent;
        }

        if (!hasUnsavedChanges) {
            clearEditingState();
            return true;
        }

        const shouldDiscard = window.confirm("You have unsaved changes. Discard them?");
        if (!shouldDiscard) {
            return false;
        }

        clearEditingState();
        return true;
    }, [
        clearEditingState,
        editingDraftByFileName,
        editingDraftByParentId,
        editingFileName,
        editingParentId,
        getChunkContentByParentId,
        getFullDocumentContentByFileName,
    ]);

    const openDocumentTab = useCallback(
        async (fileName: string) => {
            if (!confirmDiscardUnsavedChanges()) {
                return;
            }

            setOpenTabs((previousTabs) =>
                previousTabs.includes(fileName) ? previousTabs : [...previousTabs, fileName]
            );
            setActiveTab(fileName);

            const state = tabStates[fileName];
            if (!state || !state.isInitialized) {
                await loadFileChunks(fileName, false);
            }
        },
        [confirmDiscardUnsavedChanges, loadFileChunks, tabStates]
    );

    const closeDocumentTab = useCallback((fileName: string) => {
        if (!confirmDiscardUnsavedChanges()) {
            return;
        }

        setOpenTabs((previousTabs) => {
            const index = previousTabs.indexOf(fileName);
            if (index < 0) {
                return previousTabs;
            }

            const nextTabs = previousTabs.filter((name) => name !== fileName);

            setActiveTab((previousActiveTab) => {
                if (previousActiveTab !== fileName) {
                    return previousActiveTab;
                }

                if (nextTabs.length === 0) {
                    return null;
                }

                const nextIndex = Math.min(index, nextTabs.length - 1);
                return nextTabs[nextIndex];
            });

            return nextTabs;
        });

        setSelectedParentByTab((previousSelections) => {
            if (!(fileName in previousSelections)) {
                return previousSelections;
            }

            const { [fileName]: _removedSelection, ...nextSelections } = previousSelections;
            return nextSelections;
        });
    }, [confirmDiscardUnsavedChanges]);

    const setActiveDocumentTab = useCallback(
        async (fileName: string) => {
            if (!confirmDiscardUnsavedChanges()) {
                return;
            }

            setActiveTab(fileName);
            const state = tabStates[fileName];
            if (!state || !state.isInitialized) {
                await loadFileChunks(fileName, false);
            }
        },
        [confirmDiscardUnsavedChanges, loadFileChunks, tabStates]
    );

    const loadMoreActiveTab = useCallback(async () => {
        if (!activeTab) {
            return;
        }
        await loadFileChunks(activeTab, false);
    }, [activeTab, loadFileChunks]);

    const handleRefreshDocuments = useCallback(async () => {
        if (!confirmDiscardUnsavedChanges()) {
            return;
        }

        await fetchFiles();
    }, [confirmDiscardUnsavedChanges, fetchFiles]);

    const invalidateDocumentCache = useCallback(() => {
        setIsDocsCached(false);
    }, []);

    const activeTabState = useMemo(
        () => (activeTab ? tabStates[activeTab] ?? createEmptyTabState() : null),
        [activeTab, tabStates]
    );

    const activeSelectedParentId = useMemo(
        () => (activeTab ? selectedParentByTab[activeTab] ?? null : null),
        [activeTab, selectedParentByTab]
    );

    const activeSelectedChunk = useMemo(
        () => activeTabState?.chunks.find((chunk) => chunk.parentId === activeSelectedParentId) ?? null,
        [activeSelectedParentId, activeTabState]
    );

    const editingContent = useMemo(() => {
        if (!editingParentId) {
            return "";
        }
        const fallbackContent = getChunkContentByParentId(editingParentId) ?? "";
        return editingDraftByParentId[editingParentId] ?? fallbackContent;
    }, [editingDraftByParentId, editingParentId, getChunkContentByParentId]);

    const isEditingActiveChunk = Boolean(
        activeSelectedParentId && editingParentId && activeSelectedParentId === editingParentId
    );

    const isSavingActiveChunk = Boolean(
        activeSelectedParentId && savingParentId && activeSelectedParentId === savingParentId
    );

    const isActiveChunkDirty = useMemo(() => {
        if (!activeSelectedParentId || !isEditingActiveChunk) {
            return false;
        }

        const originalContent = activeSelectedChunk?.content ?? "";
        const draftContent = editingDraftByParentId[activeSelectedParentId] ?? originalContent;
        return draftContent !== originalContent;
    }, [activeSelectedChunk, activeSelectedParentId, editingDraftByParentId, isEditingActiveChunk]);

    const selectActiveTabChunk = useCallback(
        (parentId: string) => {
            if (!activeTab) {
                return;
            }

            if (editingParentId && parentId !== editingParentId && !confirmDiscardUnsavedChanges()) {
                return;
            }

            setSelectedParentByTab((previousSelections) => ({
                ...previousSelections,
                [activeTab]: parentId,
            }));
            setSaveError(null);
        },
        [activeTab, confirmDiscardUnsavedChanges, editingParentId]
    );

    const startEditingActiveChunk = useCallback(() => {
        if (!activeSelectedChunk) {
            return;
        }

        setEditingParentId(activeSelectedChunk.parentId);
        setEditingDraftByParentId((previousDrafts) => ({
            ...previousDrafts,
            [activeSelectedChunk.parentId]: previousDrafts[activeSelectedChunk.parentId] ?? activeSelectedChunk.content,
        }));
        setSaveError(null);
    }, [activeSelectedChunk]);

    const setActiveEditingContent = useCallback(
        (nextContent: string) => {
            if (!editingParentId) {
                return;
            }

            setEditingDraftByParentId((previousDrafts) => ({
                ...previousDrafts,
                [editingParentId]: nextContent,
            }));
        },
        [editingParentId]
    );

    const cancelEditingActiveChunk = useCallback(() => {
        clearEditingState();
    }, [clearEditingState]);

    const saveEditingActiveChunk = useCallback(async () => {
        if (!activeTab || !editingParentId || savingParentId) {
            return false;
        }

        const originalContent = getChunkContentByParentId(editingParentId) ?? "";
        const draftContent = editingDraftByParentId[editingParentId] ?? originalContent;
        const trimmedDraftContent = draftContent.trim();

        if (!trimmedDraftContent) {
            setSaveError("Content cannot be empty.");
            return false;
        }

        if (draftContent === originalContent) {
            clearEditingState();
            return true;
        }

        setSavingParentId(editingParentId);
        setSaveError(null);

        try {
            const response = await axios.put<UpdateParentChunkResponse>(
                `${API_BASE}/api/modifications/parent-chunks/${editingParentId}`,
                {
                    fileName: activeTab,
                    content: draftContent,
                }
            );

            const updatedParentId = response.data.parentId;
            await loadFileChunks(activeTab, true);

            setSelectedParentByTab((previousSelections) => ({
                ...previousSelections,
                [activeTab]: updatedParentId,
            }));
            clearEditingState();
            return true;
        } catch (error) {
            console.error("Error saving parent chunk:", error);
            setSaveError("Failed to save document changes. Please try again.");
            return false;
        } finally {
            setSavingParentId(null);
        }
    }, [
        activeTab,
        clearEditingState,
        editingDraftByParentId,
        editingParentId,
        getChunkContentByParentId,
        loadFileChunks,
        savingParentId,
    ]);

    const editingDocumentContent = useMemo(() => {
        if (!editingFileName) {
            return "";
        }
        const fallbackContent = getFullDocumentContentByFileName(editingFileName);
        return editingDraftByFileName[editingFileName] ?? fallbackContent;
    }, [editingDraftByFileName, editingFileName, getFullDocumentContentByFileName]);

    const isEditingActiveDocument = Boolean(activeTab && editingFileName && activeTab === editingFileName);

    const isSavingActiveDocument = Boolean(activeTab && savingFileName && activeTab === savingFileName);

    const isActiveDocumentDirty = useMemo(() => {
        if (!activeTab || !isEditingActiveDocument) {
            return false;
        }

        const originalContent = getFullDocumentContentByFileName(activeTab);
        const draftContent = editingDraftByFileName[activeTab] ?? originalContent;
        return draftContent !== originalContent;
    }, [activeTab, editingDraftByFileName, getFullDocumentContentByFileName, isEditingActiveDocument]);

    const startEditingActiveDocument = useCallback(() => {
        if (!activeTab || !activeTabState?.chunks.length) {
            return;
        }

        const fullContent = getFullDocumentContentByFileName(activeTab);
        setEditingFileName(activeTab);
        setEditingDraftByFileName((previousDrafts) => ({
            ...previousDrafts,
            [activeTab]: previousDrafts[activeTab] ?? fullContent,
        }));
        setSaveError(null);
    }, [activeTab, activeTabState?.chunks.length, getFullDocumentContentByFileName]);

    const setActiveEditingDocumentContent = useCallback(
        (nextContent: string) => {
            if (!editingFileName) {
                return;
            }

            setEditingDraftByFileName((previousDrafts) => ({
                ...previousDrafts,
                [editingFileName]: nextContent,
            }));
        },
        [editingFileName]
    );

    const cancelEditingActiveDocument = useCallback(() => {
        clearEditingState();
    }, [clearEditingState]);

    const saveEditingActiveDocument = useCallback(async () => {
        if (!activeTab || !editingFileName || activeTab !== editingFileName || savingFileName) {
            return false;
        }

        const fileId = getFileIdByName(activeTab);
        if (!fileId) {
            setSaveError(`Missing file ID for ${activeTab}. Please refresh documents.`);
            return false;
        }

        const originalContent = getFullDocumentContentByFileName(activeTab);
        const draftContent = editingDraftByFileName[activeTab] ?? originalContent;
        const trimmedDraftContent = draftContent.trim();

        if (!trimmedDraftContent) {
            setSaveError("Content cannot be empty.");
            return false;
        }

        if (draftContent === originalContent) {
            clearEditingState();
            return true;
        }

        setSavingFileName(activeTab);
        setSaveError(null);

        try {
            const response = await axios.put<UpdateFileResponse>(`${API_BASE}/api/modifications/update-file/${fileId}`, {
                fileName: activeTab,
                content: draftContent,
            });

            const updated = response.data;
            const localParentId =
                tabStates[activeTab]?.chunks[0]?.parentId ?? `local-${updated.fileId || activeTab}`;

            setFiles((previousFiles) =>
                previousFiles.map((file) =>
                    file.fileName === activeTab || file.fileId === updated.previousFileId
                        ? {
                              ...file,
                              fileId: updated.fileId,
                              fileName: updated.fileName,
                              previewTexts: buildPreviewText(updated.content),
                          }
                        : file
                )
            );

            setTabStates((previousStates) => {
                const current = previousStates[activeTab] ?? createEmptyTabState();
                return {
                    ...previousStates,
                    [activeTab]: {
                        ...current,
                        chunks: [
                            {
                                parentId: localParentId,
                                content: updated.content,
                                size: updated.size,
                            },
                        ],
                        hasMore: false,
                        nextCursor: null,
                        isLoading: false,
                        isInitialized: true,
                        error: null,
                    },
                };
            });

            setSelectedParentByTab((previousSelections) => ({
                ...previousSelections,
                [activeTab]: localParentId,
            }));

            clearEditingState();
            return true;
        } catch (error) {
            console.error("Error saving full document:", error);
            setSaveError("Failed to save document changes. Please try again.");
            return false;
        } finally {
            setSavingFileName(null);
        }
    }, [
        activeTab,
        clearEditingState,
        editingDraftByFileName,
        editingFileName,
        getFileIdByName,
        getFullDocumentContentByFileName,
        savingFileName,
        tabStates,
    ]);

    return {
        files,
        isLoadingFiles,
        fileListError,
        openTabs,
        activeTab,
        activeTabState,
        tabStates,
        fetchFiles,
        handleRefreshDocuments,
        invalidateDocumentCache,
        openDocumentTab,
        closeDocumentTab,
        setActiveDocumentTab,
        loadMoreActiveTab,
        activeSelectedParentId,
        editingContent,
        isEditingActiveChunk,
        isSavingActiveChunk,
        isActiveChunkDirty,
        saveError,
        selectActiveTabChunk,
        startEditingActiveChunk,
        setActiveEditingContent,
        cancelEditingActiveChunk,
        saveEditingActiveChunk,
        editingDocumentContent,
        isEditingActiveDocument,
        isSavingActiveDocument,
        isActiveDocumentDirty,
        startEditingActiveDocument,
        setActiveEditingDocumentContent,
        cancelEditingActiveDocument,
        saveEditingActiveDocument,
    };
}
