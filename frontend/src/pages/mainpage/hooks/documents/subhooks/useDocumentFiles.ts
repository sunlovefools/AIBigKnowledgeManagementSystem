import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

const PREVIEW_AUTO_DRAIN_SAFETY_LIMIT = 500;

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
    const previewNextCursorRef = useRef<string | null>(null);
    const previewRequestInFlightRef = useRef(false);
    const previewAutoDrainInProgressRef = useRef(false);
    const previewPaginationSessionRef = useRef(0);

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

    const setPreviewPaginationState = useCallback((nextCursor: string | null) => {
        previewNextCursorRef.current = nextCursor;
        setPreviewNextCursor(nextCursor);
        setPreviewHasMore(Boolean(nextCursor));
    }, []);

    const applyPreviewPage = useCallback((reset: boolean, response: Awaited<ReturnType<typeof getAllPreviewFiles>>) => {
        setIsDocsCached(true);
        const resolvedNextCursor = response.nextCursor ?? null;
        setPreviewPaginationState(resolvedNextCursor);
        setFilesState((prev) =>
            reset
                ? replaceFilesFromSidebarSummaries(prev, response.files)
                : appendFilesFromSidebarSummaries(prev, response.files)
        );
        setChunkAsyncByFileId((prev) => syncChunkAsyncIndex(prev, response.files));
    }, [setPreviewPaginationState]);

    const requestPreviewPage = useCallback(async (
        cursor: string | null,
        reset: boolean,
        sessionId: number
    ) => {
        if (previewRequestInFlightRef.current) return null;
        previewRequestInFlightRef.current = true;
        try {
            const response = await getAllPreviewFiles(cursor);
            if (sessionId !== previewPaginationSessionRef.current) return null;
            applyPreviewPage(reset, response);
            return response;
        } catch {
            if (reset) setFileListError("Failed to load files from vector database.");
            return null;
        } finally {
            previewRequestInFlightRef.current = false;
        }
    }, [applyPreviewPage]);

    const autoDrainPreviewPages = useCallback((sessionId: number) => {
        if (previewAutoDrainInProgressRef.current) return;
        previewAutoDrainInProgressRef.current = true;
        setIsLoadingMoreFiles(true);

        void (async () => {
            const seenCursors = new Set<string>();
            let cycles = 0;

            try {
                while (sessionId === previewPaginationSessionRef.current) {
                    const cursor = previewNextCursorRef.current;
                    if (!cursor) break;

                    if (seenCursors.has(cursor)) {
                        console.warn("[useDocumentFiles] Stopping preview auto-drain due to repeated cursor token.");
                        setPreviewPaginationState(null);
                        break;
                    }
                    if (cycles >= PREVIEW_AUTO_DRAIN_SAFETY_LIMIT) {
                        console.warn(
                            `[useDocumentFiles] Stopping preview auto-drain after reaching safety cap (${PREVIEW_AUTO_DRAIN_SAFETY_LIMIT}).`
                        );
                        break;
                    }

                    seenCursors.add(cursor);
                    cycles += 1;
                    const page = await requestPreviewPage(cursor, false, sessionId);
                    if (!page) break;
                }
            } finally {
                if (sessionId === previewPaginationSessionRef.current) {
                    setIsLoadingMoreFiles(false);
                }
                previewAutoDrainInProgressRef.current = false;
            }
        })();
    }, [requestPreviewPage, setPreviewPaginationState]);

    // Loads one sidebar page. reset=true fetches first page and starts auto-drain; false fetches one fallback page.
    const fetchFiles = useCallback(async (reset = true) => {
        if (reset) {
            if (isLoadingFiles || previewRequestInFlightRef.current) return;

            previewPaginationSessionRef.current += 1;
            const sessionId = previewPaginationSessionRef.current;
            previewAutoDrainInProgressRef.current = false;

            setIsLoadingFiles(true);
            setIsLoadingMoreFiles(false);
            setFileListError(null);
            setPreviewPaginationState(null);

            try {
                const firstPage = await requestPreviewPage(null, true, sessionId);
                if (firstPage?.nextCursor) {
                    autoDrainPreviewPages(sessionId);
                }
            } finally {
                setIsLoadingFiles(false);
            }
            return;
        }

        // Manual fallback pagination (e.g. scroll) while auto-drain is idle.
        if (
            isLoadingFiles
            || isLoadingMoreFiles
            || previewAutoDrainInProgressRef.current
            || previewRequestInFlightRef.current
        ) {
            return;
        }
        const cursor = previewNextCursorRef.current;
        if (!cursor) return;

        const sessionId = previewPaginationSessionRef.current;
        setIsLoadingMoreFiles(true);
        try {
            await requestPreviewPage(cursor, false, sessionId);
        } finally {
            if (!previewAutoDrainInProgressRef.current) {
                setIsLoadingMoreFiles(false);
            }
        }
    }, [autoDrainPreviewPages, isLoadingFiles, isLoadingMoreFiles, requestPreviewPage, setPreviewPaginationState]);

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
        previewPaginationSessionRef.current += 1;
        previewAutoDrainInProgressRef.current = false;
        previewRequestInFlightRef.current = false;
        previewNextCursorRef.current = null;
        setIsDocsCached(false);
        setPreviewHasMore(true);
        setPreviewNextCursor(null);
        setIsLoadingMoreFiles(false);
    }, []);

    useEffect(() => {
        return () => {
            previewPaginationSessionRef.current += 1;
            previewAutoDrainInProgressRef.current = false;
            previewRequestInFlightRef.current = false;
            previewNextCursorRef.current = null;
        };
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
