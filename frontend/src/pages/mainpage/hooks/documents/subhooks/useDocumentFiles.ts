import { useCallback, useEffect, useMemo, useState } from "react";
import type {
    FileContentAsyncState,
    FileEntry,
    FileTabAsyncState,
    FileTabState,
    FilesState,
    ParentChunkContent,
} from "../../../types";
import { getAllPreviewFiles, getFileChunks } from "../api/documentsApi";
import { createEmptyContentAsyncState, createEmptyContentState, createEmptyFilesState, toSidebarFileSummary } from "../state/factories";
import {
    appendFilesFromSidebarSummaries,
    markFileChunkLoading,
    patchChunkContent,
    replaceFilesFromSidebarSummaries,
    syncChunkAsyncIndex,
} from "../state/transitions";

type UseDocumentFilesParams = {
    isModificationPanelOpen: boolean;
};

// Owns file list state, tab state, and per-file chunk loading/pagination.
export function useDocumentFiles({ isModificationPanelOpen }: UseDocumentFilesParams) {
    const [filesState, setFilesState] = useState<FilesState>(createEmptyFilesState());
    const [chunkAsyncByFileId, setChunkAsyncByFileId] = useState<Record<string, FileContentAsyncState>>({});
    const [isLoadingFiles, setIsLoadingFiles] = useState(false);
    const [isLoadingMoreFiles, setIsLoadingMoreFiles] = useState(false);
    const [fileListError, setFileListError] = useState<string | null>(null);
    const [isDocsCached, setIsDocsCached] = useState(false);
    const [previewHasMore, setPreviewHasMore] = useState(true);
    const [previewNextCursor, setPreviewNextCursor] = useState<string | null>(null);
    const [deletingFileId, setDeletingFileId] = useState<string | null>(null);

    const files = useMemo(
        () =>
            filesState.sidebarFileIds
                .map((fileId) => filesState.byId[fileId])
                .filter((entry): entry is FileEntry => Boolean(entry))
                .map(toSidebarFileSummary),
        [filesState]
    );
    const openTabs = filesState.openTabIds;
    const activeTab = filesState.activeFileId;

    const getFileNameById = useCallback(
        (fileId: string) => filesState.byId[fileId]?.fileName ?? fileId,
        [filesState]
    );
    const getFileIdByName = useCallback(
        (fileName: string) =>
            filesState.sidebarFileIds.find((fileId) => filesState.byId[fileId]?.fileName === fileName) ?? null,
        [filesState]
    );
    const getContentStateById = useCallback(
        (fileId: string) => filesState.byId[fileId]?.contentState ?? createEmptyContentState(),
        [filesState]
    );
    const getChunkAsyncById = useCallback(
        (fileId: string) => chunkAsyncByFileId[fileId] ?? createEmptyContentAsyncState(),
        [chunkAsyncByFileId]
    );

    // Loads one sidebar page. reset=true fetches first page; false appends the next page.
    const fetchFiles = useCallback(async (reset = true) => {
        if (reset && (isLoadingFiles || isLoadingMoreFiles)) return;

        if (!reset) {
            if (isLoadingFiles || isLoadingMoreFiles || !previewHasMore) return;
            setIsLoadingMoreFiles(true);
        } else {
            setIsLoadingFiles(true);
            setFileListError(null);
        }

        try {
            const response = await getAllPreviewFiles(reset ? null : previewNextCursor);
            setIsDocsCached(true);
            setPreviewHasMore(response.hasMore);
            setPreviewNextCursor(response.nextCursor);
            setFilesState((prev) =>
                reset
                    ? replaceFilesFromSidebarSummaries(prev, response.files)
                    : appendFilesFromSidebarSummaries(prev, response.files)
            );
            setChunkAsyncByFileId((prev) => syncChunkAsyncIndex(prev, response.files));
        } catch {
            if (reset) setFileListError("Failed to load files from vector database.");
        } finally {
            if (reset) setIsLoadingFiles(false);
            else setIsLoadingMoreFiles(false);
        }
    }, [isLoadingFiles, isLoadingMoreFiles, previewHasMore, previewNextCursor]);

    useEffect(() => {
        // Delay initial fetch until the panel is actually opened.
        if (isModificationPanelOpen && !isDocsCached) void fetchFiles(true);
    }, [fetchFiles, isDocsCached, isModificationPanelOpen]);

    const loadMoreFiles = useCallback(async () => {
        await fetchFiles(false);
    }, [fetchFiles]);

    const loadFileChunks = useCallback(
        async (fileId: string, reset = false): Promise<ParentChunkContent[]> => {
            const current = getContentStateById(fileId);
            const currentAsync = getChunkAsyncById(fileId);
            // Prevent duplicate in-flight loads and stop when pagination is exhausted.
            if (!reset && currentAsync.isLoading) return current.chunks;
            if (!reset && currentAsync.isInitialized && !current.hasMore) return current.chunks;

            setFilesState((prev) => markFileChunkLoading(prev, fileId, reset));
            setChunkAsyncByFileId((prev) => ({
                ...prev,
                [fileId]: {
                    ...(prev[fileId] ?? createEmptyContentAsyncState()),
                    isLoading: true,
                    error: null,
                    ...(reset ? { isInitialized: false } : {}),
                },
            }));

            const cursor = reset ? null : current.nextCursor;
            try {
                const response = await getFileChunks(fileId, cursor);
                const existing = reset ? [] : getContentStateById(fileId).chunks;
                const merged = reset ? response.chunks : [...existing, ...response.chunks];
                // Deduplicate by parentId in case the backend returns overlapping windows.
                const deduped = Array.from(new Map(merged.map((c) => [c.parentId, c])).values());

                setFilesState((prev) =>
                    patchChunkContent(prev, fileId, deduped, response.hasMore, response.nextCursor)
                );
                setChunkAsyncByFileId((prev) => ({
                    ...prev,
                    [fileId]: {
                        ...(prev[fileId] ?? createEmptyContentAsyncState()),
                        isLoading: false,
                        isInitialized: true,
                        error: null,
                    },
                }));
                return deduped;
            } catch {
                const fileName = getFileNameById(fileId);
                setChunkAsyncByFileId((prev) => ({
                    ...prev,
                    [fileId]: {
                        ...(prev[fileId] ?? createEmptyContentAsyncState()),
                        isLoading: false,
                        isInitialized: true,
                        error: `Failed to load content for ${fileName}.`,
                    },
                }));
                return [];
            }
        },
        [getChunkAsyncById, getContentStateById, getFileNameById]
    );

    const activeTabData = useMemo<FileTabState | null>(
        () => (activeTab ? getContentStateById(activeTab) : null),
        [activeTab, getContentStateById]
    );
    const activeTabAsync = useMemo<FileTabAsyncState | null>(
        () => (activeTab ? getChunkAsyncById(activeTab) : null),
        [activeTab, getChunkAsyncById]
    );

    const invalidateDocumentCache = useCallback(() => {
        setIsDocsCached(false);
        setPreviewHasMore(true);
        setPreviewNextCursor(null);
    }, []);

    return {
        filesState,
        setFilesState,
        chunkAsyncByFileId,
        setChunkAsyncByFileId,
        isLoadingFiles,
        isLoadingMoreFiles,
        hasMoreFiles: previewHasMore,
        fileListError,
        deletingFileId,
        setDeletingFileId,
        files,
        openTabs,
        activeTab,
        activeTabData,
        activeTabAsync,
        fetchFiles,
        loadMoreFiles,
        loadFileChunks,
        invalidateDocumentCache,
        getFileNameById,
        getFileIdByName,
        getContentStateById,
        getChunkAsyncById,
    };
}
