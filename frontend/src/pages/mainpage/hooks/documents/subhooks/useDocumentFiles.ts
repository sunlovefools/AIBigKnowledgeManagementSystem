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
import { markFileChunkLoading, patchChunkContent, replaceFilesFromSidebarSummaries, syncChunkAsyncIndex } from "../state/transitions";

const FILE_CHUNK_CACHE_LIMIT = 5;

type CachedFileChunks = {
    chunks: ParentChunkContent[];
    hasMore: boolean;
    nextCursor: string | null;
};

type FileChunksPage = Awaited<ReturnType<typeof getFileChunks>>;

const documentStateCache: Record<
    string,
    {
        filesState: FilesState;
        chunkAsyncByFileId: Record<string, FileContentAsyncState>;
        isDocsCached: boolean;
    }
> = {};
const fileChunkCache = new Map<string, CachedFileChunks>();
const inFlightChunkPages = new Map<string, Promise<FileChunksPage>>();

function buildFileChunkCacheKey(collectionId: string | null, fileId: string): string {
    const scope = collectionId ?? "__default_collection__";
    return `${scope}::${fileId}`;
}

// Owns file list state, tab state, and per-file chunk loading/pagination.
export function useDocumentFiles(activeCollectionId: string | null) {
    const activeCacheKey = activeCollectionId ?? "__default_collection__";
    const initialCachedState = documentStateCache[activeCacheKey];
    const [filesState, setFilesState] = useState<FilesState>(initialCachedState?.filesState ?? createEmptyFilesState());
    const [chunkAsyncByFileId, setChunkAsyncByFileId] = useState<Record<string, FileContentAsyncState>>(initialCachedState?.chunkAsyncByFileId ?? {});
    const [isLoadingFiles, setIsLoadingFiles] = useState(false);
    const [fileListError, setFileListError] = useState<string | null>(null);
    const [isDocsCached, setIsDocsCached] = useState(initialCachedState?.isDocsCached ?? false);
    const [deletingFileId, setDeletingFileId] = useState<string | null>(null);
    const cacheRef = useRef(documentStateCache);
    const fileChunkCacheRef = useRef(fileChunkCache);
    const inFlightChunkPagesRef = useRef(inFlightChunkPages);

    const getCachedFileChunks = useCallback(
        (fileId: string): CachedFileChunks | null => {
            const key = buildFileChunkCacheKey(activeCollectionId, fileId);
            const cached = fileChunkCacheRef.current.get(key);
            if (!cached) return null;
            // Refresh recency for LRU behavior.
            fileChunkCacheRef.current.delete(key);
            fileChunkCacheRef.current.set(key, cached);
            return cached;
        },
        [activeCollectionId]
    );

    const setCachedFileChunks = useCallback(
        (fileId: string, value: CachedFileChunks) => {
            const key = buildFileChunkCacheKey(activeCollectionId, fileId);
            fileChunkCacheRef.current.delete(key);
            fileChunkCacheRef.current.set(key, value);
            while (fileChunkCacheRef.current.size > FILE_CHUNK_CACHE_LIMIT) {
                const oldestKey = fileChunkCacheRef.current.keys().next().value;
                if (!oldestKey) break;
                fileChunkCacheRef.current.delete(oldestKey);
            }
        },
        [activeCollectionId]
    );

    const invalidateCachedFileChunks = useCallback(
        (fileId: string) => {
            fileChunkCacheRef.current.delete(buildFileChunkCacheKey(activeCollectionId, fileId));
        },
        [activeCollectionId]
    );

    const fetchChunkPage = useCallback(
        (fileId: string, cursor: string | null): Promise<FileChunksPage> => {
            const key = `${buildFileChunkCacheKey(activeCollectionId, fileId)}::${cursor ?? "__first_page__"}`;
            const existing = inFlightChunkPagesRef.current.get(key);
            if (existing) return existing;

            const request = getFileChunks(fileId, cursor, activeCollectionId).finally(() => {
                inFlightChunkPagesRef.current.delete(key);
            });
            inFlightChunkPagesRef.current.set(key, request);
            return request;
        },
        [activeCollectionId]
    );

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

    // Loads sidebar metadata once per cache cycle.
    const fetchFiles = useCallback(async () => {
        setIsLoadingFiles(true);
        setFileListError(null);
        try {
            const incoming = await getAllPreviewFiles(activeCollectionId);
            setIsDocsCached(true);
            setFilesState((prev) => replaceFilesFromSidebarSummaries(prev, incoming));
            setChunkAsyncByFileId((prev) => syncChunkAsyncIndex(prev, incoming));
        } catch {
            setFileListError("Failed to load files from vector database.");
        } finally {
            setIsLoadingFiles(false);
        }
    }, [activeCollectionId]);

    useEffect(() => {
        const cached = cacheRef.current[activeCacheKey];
        if (cached) {
            setFilesState(cached.filesState);
            setChunkAsyncByFileId(cached.chunkAsyncByFileId);
            setIsDocsCached(cached.isDocsCached);
            setFileListError(null);
            return;
        }

        setFilesState(createEmptyFilesState());
        setChunkAsyncByFileId({});
        setFileListError(null);
        setIsDocsCached(false);
    }, [activeCacheKey]);

    useEffect(() => {
        cacheRef.current[activeCacheKey] = {
            filesState,
            chunkAsyncByFileId,
            isDocsCached,
        };
    }, [activeCacheKey, chunkAsyncByFileId, filesState, isDocsCached]);

    useEffect(() => {
        // Always revalidate in the background. Cached files remain visible while
        // fresh sidebar metadata is fetched.
        void fetchFiles();
    }, [fetchFiles]);

    const loadFileChunks = useCallback(
        async (fileId: string, reset = false): Promise<ParentChunkContent[]> => {
            const current = getContentStateById(fileId);
            const currentAsync = getChunkAsyncById(fileId);
            const cached = getCachedFileChunks(fileId);

            // Hard resets for tab-open can reuse cached chunks and skip network.
            if (reset && cached) {
                setFilesState((prev) =>
                    patchChunkContent(prev, fileId, cached.chunks, cached.hasMore, cached.nextCursor)
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
                return cached.chunks;
            }

            // First-open path can hydrate from cache as well.
            if (!reset && !currentAsync.isInitialized && cached) {
                setFilesState((prev) =>
                    patchChunkContent(prev, fileId, cached.chunks, cached.hasMore, cached.nextCursor)
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
                return cached.chunks;
            }

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
                const response = await fetchChunkPage(fileId, cursor);
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
                setCachedFileChunks(fileId, {
                    chunks: deduped,
                    hasMore: response.hasMore,
                    nextCursor: response.nextCursor,
                });
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
        [fetchChunkPage, getCachedFileChunks, getChunkAsyncById, getContentStateById, getFileNameById, setCachedFileChunks]
    );

    const loadFileChunksUntilParent = useCallback(
        async (fileId: string, parentId: string): Promise<ParentChunkContent[]> => {
            const cached = getCachedFileChunks(fileId);
            if (cached && (cached.chunks.some((chunk) => chunk.parentId === parentId) || !cached.hasMore)) {
                setFilesState((prev) =>
                    patchChunkContent(prev, fileId, cached.chunks, cached.hasMore, cached.nextCursor)
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
                return cached.chunks;
            }

            setFilesState((prev) => markFileChunkLoading(prev, fileId, true));
            setChunkAsyncByFileId((prev) => ({
                ...prev,
                [fileId]: {
                    ...(prev[fileId] ?? createEmptyContentAsyncState()),
                    isLoading: true,
                    isInitialized: false,
                    error: null,
                },
            }));

            let cursor: string | null = null;
            let hasMore = true;
            let nextCursor: string | null = null;
            let chunks: ParentChunkContent[] = [];
            let safetyCounter = 0;

            try {
                while (hasMore && safetyCounter < 200) {
                    const response = await fetchChunkPage(fileId, cursor);
                    chunks = Array.from(
                        new Map([...chunks, ...response.chunks].map((chunk) => [chunk.parentId, chunk])).values()
                    );
                    hasMore = response.hasMore;
                    nextCursor = response.nextCursor;

                    setFilesState((prev) =>
                        patchChunkContent(prev, fileId, chunks, hasMore, nextCursor)
                    );
                    setCachedFileChunks(fileId, {
                        chunks,
                        hasMore,
                        nextCursor,
                    });

                    if (chunks.some((chunk) => chunk.parentId === parentId)) break;
                    if (!hasMore || !nextCursor) break;
                    cursor = nextCursor;
                    safetyCounter += 1;
                }

                setChunkAsyncByFileId((prev) => ({
                    ...prev,
                    [fileId]: {
                        ...(prev[fileId] ?? createEmptyContentAsyncState()),
                        isLoading: false,
                        isInitialized: true,
                        error: null,
                    },
                }));
                return chunks;
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
                return chunks;
            }
        },
        [fetchChunkPage, getCachedFileChunks, getFileNameById, setCachedFileChunks]
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
        delete cacheRef.current[activeCacheKey];
        setIsDocsCached(false);
    }, [activeCacheKey]);

    return {
        filesState,
        setFilesState,
        chunkAsyncByFileId,
        setChunkAsyncByFileId,
        isLoadingFiles,
        fileListError,
        deletingFileId,
        setDeletingFileId,
        files,
        openTabs,
        activeTab,
        activeTabData,
        activeTabAsync,
        fetchFiles,
        loadFileChunks,
        loadFileChunksUntilParent,
        invalidateDocumentCache,
        invalidateCachedFileChunks,
        getFileNameById,
        getFileIdByName,
        getContentStateById,
        getChunkAsyncById,
    };
}
