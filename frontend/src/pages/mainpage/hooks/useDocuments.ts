import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import type { FileTabState, ParentChunkContent, SidebarFileSummary } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");
const PAGE_SIZE = 7;

function createEmptyTabState(): FileTabState {
    return {
        chunks: [],
        hasMore: true,
        nextCursor: null,
        isLoading: false,
        isInitialized: false,
        totalParentChunks: 0,
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

    const fetchFiles = useCallback(async () => {
        setIsLoadingFiles(true);
        setFileListError(null);
        try {
            const response = await axios.get(`${API_BASE}/api/modifications/files`);
            const incomingFiles = (response.data.files ?? []) as SidebarFileSummary[];
            setFiles(incomingFiles);
            setIsDocsCached(true);

            const validNames = new Set(incomingFiles.map((item) => item.fileName));

            setOpenTabs((previousTabs) => previousTabs.filter((fileName) => validNames.has(fileName)));
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
                const response = await axios.get(`${API_BASE}/api/modifications/file-chunks`, {
                    params: {
                        fileName,
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
                            totalParentChunks: Number(response.data.totalParentChunks ?? 0),
                            isLoading: false,
                            isInitialized: true,
                            error: null,
                        },
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
        [tabStates]
    );

    const openDocumentTab = useCallback(
        async (fileName: string) => {
            setOpenTabs((previousTabs) =>
                previousTabs.includes(fileName) ? previousTabs : [...previousTabs, fileName]
            );
            setActiveTab(fileName);

            const state = tabStates[fileName];
            if (!state || !state.isInitialized) {
                await loadFileChunks(fileName, false);
            }
        },
        [loadFileChunks, tabStates]
    );

    const closeDocumentTab = useCallback((fileName: string) => {
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
    }, []);

    const setActiveDocumentTab = useCallback(
        async (fileName: string) => {
            setActiveTab(fileName);
            const state = tabStates[fileName];
            if (!state || !state.isInitialized) {
                await loadFileChunks(fileName, false);
            }
        },
        [loadFileChunks, tabStates]
    );

    const loadMoreActiveTab = useCallback(async () => {
        if (!activeTab) {
            return;
        }
        await loadFileChunks(activeTab, false);
    }, [activeTab, loadFileChunks]);

    const handleRefreshDocuments = useCallback(async () => {
        await fetchFiles();
    }, [fetchFiles]);

    const invalidateDocumentCache = useCallback(() => {
        setIsDocsCached(false);
    }, []);

    const activeTabState = useMemo(
        () => (activeTab ? tabStates[activeTab] ?? createEmptyTabState() : null),
        [activeTab, tabStates]
    );

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
    };
}
