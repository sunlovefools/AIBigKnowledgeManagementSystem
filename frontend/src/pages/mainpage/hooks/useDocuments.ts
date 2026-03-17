import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import type {
    AgentProposal,
    FileContentAsyncState,
    FileContentState,
    FileEntry,
    FileTabAsyncState,
    FilesState,
    HighlightedSelection,
    ParentChunkContent,
    SidebarFileSummary,
} from "../types";
import { normalizeMarkdownForEditor } from "../utils/markdownEditor";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");
// Number of parent chunks requested per backend page for one file.
const PAGE_SIZE = 7;

type UpdateParentChunkResponse = {
    parentId: string;
    previousParentId: string;
    fileName: string;
    content: string;
    size: number;
    chunks: number;
};

type BatchUpdateParentChunksResponse = {
    fileId: string;
    fileName: string;
    updatedCount: number;
    results: UpdateParentChunkResponse[];
    requiresReload: boolean;
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

type DeleteFileResponse = {
    fileId: string;
    fileName: string;
    deletedParentChunks: number;
    deletedChildChunks: number;
    s3Status: "deleted" | "not_found" | "skipped" | "failed";
    s3DeletedObjects: number;
    warnings: string[];
};

type AgentModifyResponse = {
    intention: string;
    proposals: AgentProposal[];
};

type SelectionEditPreviewResponse = {
    fileId: string;
    fileName: string;
    parentId: string;
    selectedText: string;
    proposedText: string;
    startOffset: number;
    endOffset: number;
};

type RequestAgentResult = {
    ok: boolean;
    summary?: string;
    error?: string;
};

type DeleteFileResult = {
    ok: boolean;
    data?: DeleteFileResponse;
    error?: string;
};

type ChunkRange = {
    parentId: string;
    start: number;
    end: number;
    content: string;
};

function buildPreviewText(content: string): string {
    return content.replace(/\s+/g, " ").trim().slice(0, 160);
}

function createEmptyContentState(): FileContentState {
    return {
        // The loaded parent chunks for one file entry.
        chunks: [],
        // Backend pagination state for /api/retrieve/file-chunks.
        hasMore: true,
        nextCursor: null,
    };
}

function createEmptyContentAsyncState(): FileContentAsyncState {
    return {
        isLoading: false,
        // True after the first load attempt for this file tab.
        isInitialized: false,
        error: null,
    };
}

function createFileEntry(
    summary: Pick<SidebarFileSummary, "fileId" | "fileName" | "previewTexts">,
    contentState: FileContentState = createEmptyContentState()
): FileEntry {
    return {
        fileId: summary.fileId,
        fileName: summary.fileName,
        previewTexts: summary.previewTexts,
        contentState,
    };
}

function createEmptyFilesState(): FilesState {
    return {
        byId: {},
        sidebarFileIds: [],
        openTabIds: [],
        activeFileId: null,
    };
}

function toSidebarFileSummary(entry: FileEntry): SidebarFileSummary {
    return {
        fileId: entry.fileId,
        fileName: entry.fileName,
        previewTexts: entry.previewTexts,
    };
}

// Function to build one document string and a set of index ranges for each chunk
function buildChunkRanges(chunks: ParentChunkContent[]): { fullText: string; ranges: ChunkRange[] } {
    if (!chunks.length) return { fullText: "", ranges: [] };

    let cursor = 0;
    const ranges: ChunkRange[] = [];

    // Loop through the chunks and build the full document text and the index ranges for each chunk
    // Form an array with the parentId, start index, end index and content for each chunk in the document [{ parentId, start_index_words, end_index_words, content }]
    chunks.forEach((chunk, index) => {
        const start = cursor;
        const end = start + chunk.content.length;
        ranges.push({ parentId: chunk.parentId, start, end, content: chunk.content });
        cursor = end;
        if (index < chunks.length - 1) cursor += 2; // "\n\n"
    });

    return {
        fullText: chunks.map((chunk) => chunk.content).join("\n\n"),
        ranges,
    };
}

// Function to reduce the difference between original and draft into a contiguous replacement edit
function computeSingleReplaceEdit(
    original: string,
    draft: string
): { start: number; end: number; replacement: string } | null {
    // Example:
    // original: "hello world"
    // draft: "hello brave world"
    // return { start: 6, end: 6, replacement: "brave " }
    // TODO: This approach is to naive
    if (original === draft) return null;

    // Loop from the start until characters differ
    let left = 0;
    while (left < original.length && left < draft.length && original[left] === draft[left]) {
        left += 1;
    }

    // Loop from the end until characters differ
    let right = 0;
    while (
        right < original.length - left &&
        right < draft.length - left &&
        original[original.length - 1 - right] === draft[draft.length - 1 - right]
    ) {
        right += 1;
    }

    return {
        start: left,
        end: original.length - right,
        replacement: draft.slice(left, draft.length - right),
    };
}

function findTouchedRangesForEdit(
    ranges: ChunkRange[],
    edit: { start: number; end: number }
): ChunkRange[] {
    const overlapping = ranges.filter((range) => range.start < edit.end && edit.start < range.end);
    if (overlapping.length > 0) return overlapping;

    const isInsertion = edit.start === edit.end;

    // For pure insertions at exact chunk edges, choose a single owner chunk.
    // This keeps "append at end of previous chunk" from being assigned to next.
    if (isInsertion) {
        const endedHere = ranges.filter((range) => range.end === edit.start);
        const startedHere = ranges.filter((range) => range.start === edit.start);

        if (endedHere.length > 0 && startedHere.length > 0) {
            // Ambiguous contiguous boundary: prefer previous chunk by default.
            return [endedHere[endedHere.length - 1]];
        }
        if (endedHere.length > 0) return [endedHere[endedHere.length - 1]];
        if (startedHere.length > 0) return [startedHere[0]];
    } else {
        // Non-insertion edits touching chunk boundaries should update both sides.
        const boundaryTouching = ranges.filter(
            (range) => range.end === edit.start || range.start === edit.end
        );
        if (boundaryTouching.length > 0) return boundaryTouching;
    }

    // If the edit lands entirely inside the inter-chunk separator gap, map it
    // to nearby chunks so we can still use batch parent updates.
    let previous: ChunkRange | null = null;
    for (const range of ranges) {
        if (range.end <= edit.start) previous = range;
        else break;
    }
    const next = ranges.find((range) => range.start >= edit.end) ?? null;
    if (previous && next && previous.parentId !== next.parentId) {
        const isInsideGap = previous.end <= edit.start && edit.end <= next.start;
        if (isInsideGap) {
            if (isInsertion) {
                // Pick the nearest chunk edge; on ties, prefer previous chunk.
                const distanceToPrevious = edit.start - previous.end;
                const distanceToNext = next.start - edit.end;
                return distanceToPrevious <= distanceToNext ? [previous] : [next];
            }
            return [previous, next];
        }
    }

    return [];
}

// Function to 
function collectBoundaryTouchedParentIds(
    ranges: ChunkRange[],
    edit: { start: number; end: number },
    originalLength: number
): string[] {
    if (!ranges.length) return [];

    const touched = new Set<string>();
    const boundaryPositions = new Set<number>();

    // Add the boundary positions of all chunks to a set for easy lookup
    ranges.forEach((range) => {
        boundaryPositions.add(range.start);
        boundaryPositions.add(range.end);
    });

    // Special case for insertions at the very end of the document
    const isEndOfDocumentInsertion =
        edit.start === originalLength &&
        edit.end === originalLength;
    // If its the special case then we will take the previous chunk as touched chunk
    if (isEndOfDocumentInsertion) {
        const previous = ranges[ranges.length - 1];
        if (previous) touched.add(previous.parentId);
        return Array.from(touched);
    }

    const startHitsBoundary = boundaryPositions.has(edit.start);
    const endHitsBoundary = boundaryPositions.has(edit.end);
    const isInsertion = edit.start === edit.end;
    const overlapsAnyChunk = isInsertion
        ? ranges.some((range) => range.start < edit.start && edit.start < range.end)
        : ranges.some((range) => range.start < edit.end && edit.start < range.end);

    // Find the closest chunks before and after the edit position
    let previous: ChunkRange | null = null;
    for (const range of ranges) {
        if (range.end <= edit.start) previous = range;
        else break;
    }
    const next = ranges.find((range) => range.start >= edit.end) ?? null;
    const insideGap =
        previous !== null && next !== null &&
        previous.end <= edit.start &&
        edit.end <= next.start &&
        !overlapsAnyChunk;

    if (!startHitsBoundary && !endHitsBoundary && !insideGap) {
        return [];
    }

    if (previous) touched.add(previous.parentId);
    if (next) touched.add(next.parentId);

    // Fallback for exact boundary touches where only one side is detectable.
    if (touched.size === 0) {
        ranges
            .filter(
                (range) =>
                    range.start === edit.start ||
                    range.end === edit.start ||
                    range.start === edit.end ||
                    range.end === edit.end
            )
            .forEach((range) => touched.add(range.parentId));
    }

    return Array.from(touched);
}

function containsRawHtmlMarkup(text: string): boolean {
    return /<\/?[a-z][^>]*>/i.test(text);
}

// Heuristic to determine if the editor content has meaningful changes that would affect the markdown output, to avoid unnecessary save prompts.
function hasMeaningfulEditorChange(original: string, draft: string): boolean {
    return normalizeMarkdownForEditor(original) !== normalizeMarkdownForEditor(draft);
}

export function useDocuments(isModificationPanelOpen: boolean) {
    // Unified workspace state:
    // - byId[fileId] holds the complete frontend model for one file
    // - sidebarFileIds controls sidebar rendering order
    // - openTabIds controls document tab order
    // - activeFileId identifies the currently selected document
    const [filesState, setFilesState] = useState<FilesState>(createEmptyFilesState());
    const [chunkAsyncByFileId, setChunkAsyncByFileId] = useState<Record<string, FileContentAsyncState>>({});
    const [isLoadingFiles, setIsLoadingFiles] = useState(false);
    const [fileListError, setFileListError] = useState<string | null>(null);
    const [isDocsCached, setIsDocsCached] = useState(false);

    // Editing state, also keyed by fileId
    const [editingFileId, setEditingFileId] = useState<string | null>(null);
    const [editingDraftByFileId, setEditingDraftByFileId] = useState<Record<string, string>>({});
    const [savingFileId, setSavingFileId] = useState<string | null>(null);
    const [deletingFileId, setDeletingFileId] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);

    // Agent state
    const [isAgentGenerating, setIsAgentGenerating] = useState(false);
    const [agentProposals, setAgentProposals] = useState<AgentProposal[]>([]);
    const [agentAcceptedMap, setAgentAcceptedMap] = useState<Map<string, AgentProposal>>(new Map());
    const [agentSavedIds, setAgentSavedIds] = useState<Set<string>>(new Set());
    const [agentRejectedIds, setAgentRejectedIds] = useState<Set<string>>(new Set());
    const [agentSavingIds, setAgentSavingIds] = useState<Set<string>>(new Set());
    const [agentError, setAgentError] = useState<string | null>(null);
    const [agentIntention, setAgentIntention] = useState<string | null>(null);

    // Ref mirror of agentAcceptedMap — lets rejectAgentProposal (deps=[]) read the
    // latest accepted entries (including patchOffset) without a stale closure. (F01)
    const agentAcceptedMapRef = useRef(agentAcceptedMap);
    useEffect(() => { agentAcceptedMapRef.current = agentAcceptedMap; }, [agentAcceptedMap]);

    // Backward-compatible derived views consumed by the rest of the page.
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


    // Helpers: resolve between fileId and fileName
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

    // fetch all the possible files to be shown in the file sidebar 
    const fetchFiles = useCallback(async () => {
        setIsLoadingFiles(true);
        setFileListError(null);
        try {
            const response = await axios.get(`${API_BASE}/api/retrieve/all-preview-files`);
            const incoming = (response.data.files ?? []) as SidebarFileSummary[];
            setIsDocsCached(true);
            const validIds = new Set(incoming.map((f) => f.fileId)); // Form a set of valid fileIds

            // Update the filesState (Which are used to store all the files)
            setFilesState((prev) => {
                const nextById: Record<string, FileEntry> = {};
                incoming.forEach((summary) => {
                    // Refresh replaces sidebar metadata but preserves any already loaded chunk cache for that file.
                    nextById[summary.fileId] = createFileEntry(
                        summary,
                        prev.byId[summary.fileId]?.contentState ?? createEmptyContentState() // Preserve existing contentState if the file was already in state, otherwise create a new empty one
                    );
                });
                // Return the full filesState
                return {
                    byId: nextById,
                    sidebarFileIds: incoming.map((file) => file.fileId),
                    openTabIds: prev.openTabIds.filter((id) => validIds.has(id)),
                    activeFileId:
                        prev.activeFileId && validIds.has(prev.activeFileId) ? prev.activeFileId : null,
                };
            });

            // Update the chunkAsyncByFileId state (which are used to store the async loading state for each file's chunks)
            setChunkAsyncByFileId((prev) => {
                const next: Record<string, FileContentAsyncState> = {};
                incoming.forEach((summary) => {
                    next[summary.fileId] = prev[summary.fileId] ?? createEmptyContentAsyncState();
                });
                return next;
            });
        } catch {
            setFileListError("Failed to load files from vector database.");
        } finally {
            setIsLoadingFiles(false);
        }
    }, []);

    useEffect(() => {
        if (isModificationPanelOpen && !isDocsCached) void fetchFiles();
    }, [isDocsCached, isModificationPanelOpen, fetchFiles]);

    // Load all the parent chunks for one file, with pagination support.
    const loadFileChunks = useCallback(
        async (fileId: string, reset = false): Promise<ParentChunkContent[]> => {
            // Cached frontend state for this file's chunks and pagination.
            const current = getContentStateById(fileId);
            const currentAsync = getChunkAsyncById(fileId);
            // When reset=true (called by acceptAgentProposal), always fetch fresh data
            // even if a load is already in progress — the in-progress load may be
            // for a different page or stale state.
            if (!reset && currentAsync.isLoading) return current.chunks; // If its still loading then return
            if (!reset && currentAsync.isInitialized && !current.hasMore) return current.chunks; // If there is no more chunks to load then return

            // Prepare the filesState that have exisitng file entry
            setFilesState((prev) => {
                const prevEntry = prev.byId[fileId];
                const existingEntry = prev.byId[fileId] ?? createFileEntry({
                    fileId,
                    fileName: prevEntry?.fileName ?? fileId,
                    previewTexts: prevEntry?.previewTexts ?? "",
                });
                // Just to update the file's contentState to reset state if reset=true, otherwise keep the existing chunks and pagination state
                return {
                    ...prev,
                    byId: {
                        ...prev.byId,
                        [fileId]: {
                            ...existingEntry,
                            contentState: {
                                ...existingEntry.contentState,
                                ...(reset
                                    ? { chunks: [], nextCursor: null, hasMore: true }
                                    : {}),
                            },
                        },
                    },
                };
            });

            // Update the async state of the chunks for this file to loading state
            setChunkAsyncByFileId((prev) => ({
                ...prev,
                [fileId]: {
                    ...(prev[fileId] ?? createEmptyContentAsyncState()),
                    isLoading: true,
                    error: null,
                    ...(reset ? { isInitialized: false } : {}),
                },
            }));

            // Cursor from the last fetched backend page. null starts from page 1.
            const cursor = reset ? null : current.nextCursor;
            try {
                const response = await axios.get(`${API_BASE}/api/retrieve/file-chunks`, {
                    params: { fileId, limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) },
                });
                // The newly fetched backend page of parent chunks.
                const incoming = (response.data.chunks ?? []) as ParentChunkContent[];
                // The chunks already cached in the frontend for this file.
                const existing = reset ? [] : getContentStateById(fileId).chunks;
                const merged = reset ? incoming : [...existing, ...incoming];
                // Remove duplicates
                const deduped = Array.from(new Map(merged.map((c) => [c.parentId, c])).values());

                // Defensive update if the file is not loaded
                setFilesState((prev) => {
                    const prevEntry = prev.byId[fileId];
                    const existingEntry = prev.byId[fileId] ?? createFileEntry({
                        fileId,
                        fileName: prevEntry?.fileName ?? fileId,
                        previewTexts: prevEntry?.previewTexts ?? "",
                    });
                    return {
                        ...prev,
                        byId: {
                            ...prev.byId,
                            [fileId]: {
                                ...existingEntry,
                                contentState: {
                                    ...existingEntry.contentState,
                                    chunks: deduped, // Update the chunks with the deduped chunks
                                    hasMore: Boolean(response.data.hasMore),
                                    nextCursor: response.data.nextCursor ?? null,
                                },
                            },
                        },
                    };
                });

                // Reupdate the chunk async state
                setChunkAsyncByFileId((prev) => ({
                    ...prev,
                    [fileId]: {
                        ...(prev[fileId] ?? createEmptyContentAsyncState()),
                        isLoading: false,
                        isInitialized: true,
                        error: null,
                    },
                }));
                // Return the freshly loaded chunks directly — callers like
                // acceptAgentProposal need these immediately without waiting
                // for React to re-render and update filesState.
                return deduped;
            } catch {
                const fileName = getFileNameById(fileId);
                setFilesState((prev) => {
                    const prevEntry = prev.byId[fileId];
                    const existingEntry = prev.byId[fileId] ?? createFileEntry({
                        fileId,
                        fileName,
                        previewTexts: prevEntry?.previewTexts ?? "",
                    });
                    return {
                        ...prev,
                        byId: {
                            ...prev.byId,
                            [fileId]: {
                                ...existingEntry,
                                contentState: {
                                    ...existingEntry.contentState,
                                },
                            },
                        },
                    };
                });
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

    const getFullDocumentContent = useCallback(
        (fileId: string | null) => {
            if (!fileId) return "";
            // Rebuild one document by joining the loaded parent chunks in order.
            return getContentStateById(fileId).chunks
                .map((c) => c.content).join("\n\n");
        },
        [getContentStateById]
    );

    const getEditorBaselineContent = useCallback(
        (fileId: string | null) => {
            if (!fileId) return "";
            const original = getFullDocumentContent(fileId);
            if (!original) return "";
            return original;
        },
        [getFullDocumentContent]
    );

    // ── Tab management ──

    const confirmDiscardUnsavedChanges = useCallback(() => {
        if (!editingFileId) return true;
        const original = getEditorBaselineContent(editingFileId);
        const draft = editingDraftByFileId[editingFileId] ?? original;
        if (!hasMeaningfulEditorChange(original, draft)) { setEditingFileId(null); return true; }
        const ok = window.confirm("You have unsaved changes. Discard them?");
        if (ok) setEditingFileId(null);
        return ok;
    }, [editingDraftByFileId, editingFileId, getEditorBaselineContent]);

    const openDocumentTab = useCallback(
        async (fileId: string) => {
            if (!confirmDiscardUnsavedChanges()) return;
            setFilesState((prev) => ({
                ...prev,
                openTabIds: prev.openTabIds.includes(fileId) ? prev.openTabIds : [...prev.openTabIds, fileId],
                activeFileId: fileId,
            }));
            await loadFileChunks(fileId, true);
        },
        [confirmDiscardUnsavedChanges, loadFileChunks]
    );

    const closeDocumentTab = useCallback((fileId: string) => {
        if (!confirmDiscardUnsavedChanges()) return;
        setFilesState((prev) => {
            const idx = prev.openTabIds.indexOf(fileId);
            if (idx < 0) return prev;
            const nextOpenTabIds = prev.openTabIds.filter((id) => id !== fileId);
            return {
                ...prev,
                openTabIds: nextOpenTabIds,
                activeFileId:
                    prev.activeFileId !== fileId
                        ? prev.activeFileId
                        : nextOpenTabIds.length === 0
                            ? null
                            : nextOpenTabIds[Math.min(idx, nextOpenTabIds.length - 1)],
            };
        });
    }, [confirmDiscardUnsavedChanges]);

    const setActiveDocumentTab = useCallback(
        async (fileId: string) => {
            if (!confirmDiscardUnsavedChanges()) return;
            setFilesState((prev) => ({ ...prev, activeFileId: fileId }));
            const asyncState = getChunkAsyncById(fileId);
            if (!asyncState.isInitialized) await loadFileChunks(fileId, false);
        },
        [confirmDiscardUnsavedChanges, getChunkAsyncById, loadFileChunks]
    );

    const loadMoreActiveTab = useCallback(async () => {
        if (!activeTab) return;
        await loadFileChunks(activeTab, false);
    }, [activeTab, loadFileChunks]);

    const handleRefreshDocuments = useCallback(async () => {
        if (!confirmDiscardUnsavedChanges()) return;
        await fetchFiles();
    }, [confirmDiscardUnsavedChanges, fetchFiles]);

    const invalidateDocumentCache = useCallback(() => setIsDocsCached(false), []);

    const deleteFile = useCallback(
        async (fileId: string): Promise<DeleteFileResult> => {
            if (!fileId) return { ok: false, error: "Missing file ID." };
            if (deletingFileId) return { ok: false, error: "Another delete is already in progress." };

            setDeletingFileId(fileId);
            setSaveError(null);

            try {
                const response = await axios.delete<DeleteFileResponse>(
                    `${API_BASE}/api/modifications/files/${fileId}`
                );
                const deleted = response.data;

                // Capture the current file-scoped parent IDs once so every state
                // cleanup step removes the same stale proposal/document entries.
                const parentIdsForFile = new Set<string>([
                    ...getContentStateById(fileId).chunks.map((chunk) => chunk.parentId),
                    ...agentProposals
                        .filter((proposal) => proposal.fileId === fileId)
                        .map((proposal) => proposal.parentId),
                    ...Array.from(agentAcceptedMap.values())
                        .filter((proposal) => proposal.fileId === fileId)
                        .map((proposal) => proposal.parentId),
                ]);

                setFilesState((prev) => {
                    const { [fileId]: _removed, ...nextById } = prev.byId;
                    const idx = prev.openTabIds.indexOf(fileId);
                    const nextOpenTabIds = prev.openTabIds.filter((id) => id !== fileId);
                    const nextSidebarFileIds = prev.sidebarFileIds.filter((id) => id !== fileId);
                    return {
                        byId: nextById,
                        sidebarFileIds: nextSidebarFileIds,
                        openTabIds: nextOpenTabIds,
                        activeFileId:
                            prev.activeFileId !== fileId
                                ? prev.activeFileId
                                : idx < 0 || nextOpenTabIds.length === 0
                                    ? null
                                    : nextOpenTabIds[Math.min(idx, nextOpenTabIds.length - 1)],
                    };
                });
                setChunkAsyncByFileId((prev) => {
                    const next = { ...prev };
                    delete next[fileId];
                    return next;
                });
                setEditingDraftByFileId((prev) => {
                    const next = { ...prev };
                    delete next[fileId];
                    return next;
                });
                setEditingFileId((prev) => (prev === fileId ? null : prev));
                setSavingFileId((prev) => (prev === fileId ? null : prev));

                setAgentProposals((prev) => prev.filter((proposal) => proposal.fileId !== fileId));
                setAgentAcceptedMap((prev) => {
                    const next = new Map(prev);
                    for (const [parentId, proposal] of next.entries()) {
                        if (proposal.fileId === fileId) next.delete(parentId);
                    }
                    return next;
                });
                setAgentSavedIds((prev) =>
                    new Set([...prev].filter((parentId) => !parentIdsForFile.has(parentId)))
                );
                setAgentRejectedIds((prev) =>
                    new Set([...prev].filter((parentId) => !parentIdsForFile.has(parentId)))
                );
                setAgentSavingIds((prev) =>
                    new Set([...prev].filter((parentId) => !parentIdsForFile.has(parentId)))
                );

                return { ok: true, data: deleted };
            } catch (error) {
                const detail = axios.isAxiosError(error)
                    ? typeof error.response?.data?.detail === "string"
                        ? error.response.data.detail
                        : null
                    : null;
                return {
                    ok: false,
                    error: detail ?? "Failed to delete file from the knowledge base.",
                };
            } finally {
                setDeletingFileId(null);
            }
        },
        [agentAcceptedMap, agentProposals, deletingFileId, getContentStateById]
    );

    // Shortcut for the currently selected file's data state, including its chunks.
    const activeTabData = useMemo(
        () => (activeTab ? getContentStateById(activeTab) : null),
        [activeTab, getContentStateById]
    );
    const activeTabAsync = useMemo<FileTabAsyncState | null>(
        () => (activeTab ? getChunkAsyncById(activeTab) : null),
        [activeTab, getChunkAsyncById]
    );

    // Full-file save responses replace both the sidebar preview and the cached content entry.
    const applyFullFileUpdate = useCallback((updated: UpdateFileResponse) => {
        const previousContentState = getContentStateById(updated.previousFileId);
        const localParentId = previousContentState.chunks[0]?.parentId ?? `local-${updated.fileId}`;
        const refreshedEntry = createFileEntry(
            {
                fileId: updated.fileId,
                fileName: updated.fileName,
                previewTexts: buildPreviewText(updated.content),
            },
            {
                ...previousContentState,
                chunks: [{
                    parentId: localParentId,
                    content: updated.content,
                    size: updated.size,
                    pageNumbers: [0],
                }],
                hasMore: false,
                nextCursor: null,
            }
        );

        setFilesState((prev) => {
            const nextById = { ...prev.byId };
            delete nextById[updated.previousFileId];
            nextById[updated.fileId] = refreshedEntry;
            return {
                byId: nextById,
                sidebarFileIds: prev.sidebarFileIds.map((id) =>
                    id === updated.previousFileId ? updated.fileId : id
                ),
                openTabIds: prev.openTabIds.map((id) =>
                    id === updated.previousFileId ? updated.fileId : id
                ),
                activeFileId:
                    prev.activeFileId === updated.previousFileId ? updated.fileId : prev.activeFileId,
            };
        });
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
    }, [getContentStateById]);

    // ── Manual document editing ──
    const editingDocumentContent = useMemo(() => { // Return the conten shown in the editor
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

    // Start editing the active document by caching its current full content as the editing draft
    const startEditingActiveDocument = useCallback(() => {
        if (!activeTab || !activeTabData?.chunks.length) return;
        const fullContent = getEditorBaselineContent(activeTab);
        setEditingFileId(activeTab);
        setEditingDraftByFileId((prev) => ({ ...prev, [activeTab]: prev[activeTab] ?? fullContent }));
        setSaveError(null);
    // Depends on the chunks length to make sure the full document content is ready before start editing
    }, [activeTab, activeTabData?.chunks.length, getEditorBaselineContent]); 

    const setActiveEditingDocumentContent = useCallback(
        (nextContent: string) => {
            if (!editingFileId) return;
            setEditingDraftByFileId((prev) => ({ ...prev, [editingFileId]: nextContent }));
        },
        [editingFileId]
    );

    const cancelEditingActiveDocument = useCallback(() => {
        setEditingFileId(null);
        setSaveError(null);
    }, []);

    // The main saving function that based on the diff between the original content and the edited part to determine which backend API to call
    const saveEditingActiveDocument = useCallback(async () => {
        if (!activeTab || !editingFileId || activeTab !== editingFileId || savingFileId) return false;
        const fileName = getFileNameById(activeTab);
        if (!fileName) { setSaveError("Missing file name for this tab. Please refresh."); return false; }
        const state = getContentStateById(activeTab);
        const normalizedChunks = state.chunks.map((chunk) => ({
            ...chunk,
            content: normalizeMarkdownForEditor(chunk.content),
        }));
        const { fullText: original, ranges } = buildChunkRanges(normalizedChunks); // Example: original="hello world", ranges=[{ parentId: "1", start: 0, end: 5, content: "hello" }, { parentId: "2", start: 5, end: 11, content: "world" }]

        // Get the entire edited document content and make sure it is not empty and no different from the original content
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

            if (!shouldForceFullFileUpdate) {
                const editPart = computeSingleReplaceEdit(original, normalizedDraft);

                if (editPart) {
                    // 
                    const boundaryTouchedParentIds = collectBoundaryTouchedParentIds(
                        ranges,
                        editPart,
                        original.length
                    );
                    const shouldUseBoundaryRechunk = boundaryTouchedParentIds.length > 0;

                    if (shouldUseBoundaryRechunk) {
                        const batchResp = await axios.post<BatchUpdateParentChunksResponse>(
                            `${API_BASE}/api/modifications/parent-chunks/batch-update`,
                            {
                                fileId: activeTab,
                                fileName,
                                mode: "boundary_rechunk",
                                fullContent: normalizedDraft,
                                touchedParentIds: boundaryTouchedParentIds,
                            }
                        );

                        if (batchResp.data.requiresReload) {
                            await fetchFiles();
                            await loadFileChunks(activeTab, true);
                        }
                    } else {
                    // Multi-chunk update path: distribute edited window across touched chunks,
                    // then let backend re-chunk only the touched parent IDs.
                    // Get a list of chunks that are being edited based on the diff result
                    const touchedRanges = findTouchedRangesForEdit(ranges, editPart);

                    if (touchedRanges.length > 0) {
                        const firstTouchedChunk = touchedRanges[0];
                        const lastTouchedChunk = touchedRanges[touchedRanges.length - 1];
                        const draftTouchedEnd = normalizedDraft.length - (original.length - lastTouchedChunk.end);
                        const nextWindow = normalizedDraft.slice(firstTouchedChunk.start, draftTouchedEnd); // The edited window that should replace the content in the original document

                        // To store the content that needed to be updated
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
                            const batchResp = await axios.post<BatchUpdateParentChunksResponse>(
                                `${API_BASE}/api/modifications/parent-chunks/batch-update`,
                                {
                                    fileId: activeTab,
                                    fileName,
                                    mode: "fast_updates",
                                    updates,
                                }
                            );

                            const updatedRows = batchResp.data.results ?? [];
                            if (!batchResp.data.requiresReload && updatedRows.length > 0) {
                                const replacementByPreviousId = new Map(
                                    updatedRows.map((row) => [row.previousParentId, row])
                                );

                                // Apply an immediate in-memory parentId/content remap so UI state stays fresh
                                // before the follow-up fetch/load cycle completes.
                                setFilesState((prev) => {
                                    const activeEntry = prev.byId[activeTab];
                                    if (!activeEntry) return prev;

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
                                    const remappedContent = dedupedChunks
                                        .map((chunk) => chunk.content)
                                        .join("\n\n");

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
                            // If the edited content for any touched chunk is empty or whitespace-only, fallback to full-file update to avoid accidental data loss.
                            const response = await axios.put<UpdateFileResponse>(
                                `${API_BASE}/api/modifications/update-file/${activeTab}`,
                                { fileName, content: normalizedDraft }
                            );
                            const updated = response.data;
                            applyFullFileUpdate(updated);
                        }
                    } else {
                        // Fallback path for boundary-crossing or ambiguous edits.
                        const response = await axios.put<UpdateFileResponse>(
                            `${API_BASE}/api/modifications/update-file/${activeTab}`,
                            { fileName, content: normalizedDraft }
                        );
                        const updated = response.data;
                        applyFullFileUpdate(updated);
                    }
                    }
                } else {
                    const response = await axios.put<UpdateFileResponse>(
                        `${API_BASE}/api/modifications/update-file/${activeTab}`,
                        { fileName, content: normalizedDraft }
                    );
                    const updated = response.data;
                    applyFullFileUpdate(updated);
                }
            } else {
                // Fallback path for boundary-crossing or ambiguous edits.
                const response = await axios.put<UpdateFileResponse>(
                    `${API_BASE}/api/modifications/update-file/${activeTab}`,
                    { fileName, content: normalizedDraft }
                );
                const updated = response.data;
                applyFullFileUpdate(updated);
            }

            setEditingFileId(null);
            // Clear stale draft so next edit session starts from the freshly saved content
            setEditingDraftByFileId((prev) => {
                const next = { ...prev };
                delete next[activeTab];
                return next;
            });
            return true;
        } catch {
            setSaveError("Failed to save document changes. Please try again.");
            return false;
        } finally {
            setSavingFileId(null);
        }
    }, [
        activeTab,
        editingDraftByFileId,
        editingFileId,
        fetchFiles,
        getFileNameById,
        getEditorBaselineContent,
        loadFileChunks,
        savingFileId,
        applyFullFileUpdate,
        getContentStateById,
    ]);

    // ── Agent ──

    const clearAgentState = useCallback(() => {
        setAgentProposals([]);
        setAgentAcceptedMap(new Map());
        setAgentSavedIds(new Set());
        setAgentRejectedIds(new Set());
        setAgentSavingIds(new Set());
        setAgentError(null);
        setAgentIntention(null);
    }, []);

    const requestAgentEditPreview = useCallback(
        async (instruction: string, fileIds: string[] | null): Promise<RequestAgentResult> => {
            const trimmed = instruction.trim();
            if (!trimmed) return { ok: false, error: "Instruction cannot be empty." };
            if (isAgentGenerating) return { ok: false, error: "Agent is already running." };

            setIsAgentGenerating(true);
            setAgentError(null);
            setAgentProposals([]);
            setAgentAcceptedMap(new Map());
            setAgentSavedIds(new Set());
            setAgentRejectedIds(new Set());
            setAgentSavingIds(new Set());
            setAgentIntention(null);

            try {
                const response = await axios.post<AgentModifyResponse>(
                    `${API_BASE}/api/agent/modify`,
                    { instruction: trimmed, fileIds: fileIds && fileIds.length > 0 ? fileIds : null }
                );
                const { intention, proposals } = response.data;
                setAgentIntention(intention);
                setAgentProposals(proposals);
                const uniqueFiles = new Set(proposals.map((p) => p.fileId)).size;
                const summary = proposals.length > 0
                    ? `Agent found ${proposals.length} change(s) across ${uniqueFiles} file(s).`
                    : "Agent found no changes to make.";
                return { ok: true, summary };
            } catch {
                const error = "Agent failed to generate proposals. Please try again.";
                setAgentError(error);
                return { ok: false, error };
            } finally {
                setIsAgentGenerating(false);
            }
        },
        [isAgentGenerating]
    );

    const requestSelectionEditPreview = useCallback(
        async (instruction: string, selection: HighlightedSelection): Promise<RequestAgentResult> => {
            const trimmed = instruction.trim();
            if (!trimmed) return { ok: false, error: "Instruction cannot be empty." };
            if (isAgentGenerating) return { ok: false, error: "Agent is already running." };

            setFilesState((prev) => ({
                ...prev,
                openTabIds: prev.openTabIds.includes(selection.fileId)
                    ? prev.openTabIds
                    : [...prev.openTabIds, selection.fileId],
                activeFileId: selection.fileId,
            }));
            setIsAgentGenerating(true);
            setAgentError(null);
            setAgentProposals([]);
            setAgentAcceptedMap(new Map());
            setAgentSavedIds(new Set());
            setAgentRejectedIds(new Set());
            setAgentSavingIds(new Set());
            setAgentIntention("selection");

            try {
                const response = await axios.post<SelectionEditPreviewResponse>(
                    `${API_BASE}/api/modifications/selection-edit-preview`,
                    {
                        fileId: selection.fileId,
                        fileName: selection.fileName,
                        parentId: selection.parentId,
                        selectedText: selection.selectedText,
                        startOffset: selection.startOffset,
                        endOffset: selection.endOffset,
                        instruction: trimmed,
                    }
                );

                const preview = response.data;
                setAgentProposals([
                    {
                        fileId: preview.fileId,
                        fileName: preview.fileName,
                        parentId: preview.parentId,
                        original: preview.selectedText,
                        proposed: preview.proposedText,
                        source: "selection",
                        selectionStart: preview.startOffset,
                        selectionEnd: preview.endOffset,
                    },
                ]);

                const summary = preview.proposedText.trim().endsWith("?")
                    ? "The editor asked for clarification. Review the proposal in the edit panel."
                    : "Selection edit preview generated. Review the proposal in the edit panel.";
                return { ok: true, summary };
            } catch (error) {
                const detail = axios.isAxiosError(error)
                    ? typeof error.response?.data?.detail === "string"
                        ? error.response.data.detail
                        : null
                    : null;
                const requestError = detail ?? "Selected-text edit failed. Please try again.";
                setAgentError(requestError);
                return { ok: false, error: requestError };
            } finally {
                setIsAgentGenerating(false);
            }
        },
        [isAgentGenerating]
    );

    // Accept: apply partial text replacement locally. Does NOT write to DB yet.
    // Auto-loads the file's chunks if not yet in filesState.byId[fileId].contentState,
    // has to manually open a file before clicking Accept.
    const acceptAgentProposal = useCallback(async (proposal: AgentProposal) => {
        // If the chunk isn't loaded yet, trigger loading now and wait for it.
        let targetChunk = getContentStateById(proposal.fileId).chunks.find(
            (c) => c.parentId === proposal.parentId
        );
        if (!targetChunk) {
            // Open the tab and trigger loading — this is the first-time path.
            setFilesState((prev) => ({
                ...prev,
                openTabIds: prev.openTabIds.includes(proposal.fileId)
                    ? prev.openTabIds
                    : [...prev.openTabIds, proposal.fileId],
                activeFileId: proposal.fileId,
            }));
            // loadFileChunks returns the chunks directly, so we don't need to
            // wait for React state to update before reading them.
            const freshChunks = await loadFileChunks(proposal.fileId, true);
            targetChunk = freshChunks.find((c) => c.parentId === proposal.parentId);
        }
        if (!targetChunk) {
            setSaveError(`无法加载"${proposal.fileName}"的内容，请确认文件存在后重试。`);
            return;
        }

        let offset: number;
        if (
            proposal.source === "selection" &&
            proposal.selectionStart !== undefined &&
            proposal.selectionEnd !== undefined
        ) {
            offset = proposal.selectionStart;
            const currentSelection = targetChunk.content.slice(proposal.selectionStart, proposal.selectionEnd);
            if (currentSelection !== proposal.original) {
                setSaveError("The highlighted text no longer matches the current document content.");
                return;
            }
        } else {
            // B01: use indexOf to find exact position instead of .replace().
            // .replace(a, b) only changes the first occurrence silently; indexOf lets us
            // verify existence, count duplicates, and record the precise offset for revert.
            offset = targetChunk.content.indexOf(proposal.original);
            if (offset === -1) {
                // Original text no longer exists in the chunk (e.g. stale proposal after
                // another edit). Reject silently rather than writing corrupt content.
                setSaveError(`原文未在文档中找到（可能是过期的 proposal），已跳过。`);
                return;
            }

            // Warn when the original appears more than once so the user is aware only
            // the first occurrence will be patched.
            const matchCount = targetChunk.content.split(proposal.original).length - 1;
            if (matchCount > 1) {
                console.warn(
                    `[B01] "${proposal.original.slice(0, 60)}…" appears ${matchCount} times ` +
                    `in chunk ${proposal.parentId}. Only the first occurrence (offset=${offset}) ` +
                    `will be replaced.`
                );
            }
        }

        // Store offset alongside the proposal so rejectAgentProposal can restore
        // the exact position without guessing. (F01 fix)
        setAgentAcceptedMap((prev) =>
            new Map(prev).set(proposal.parentId, { ...proposal, patchOffset: offset })
        );
        // Ensure tab is open and active (no-op if already set by auto-load above).
        setFilesState((prev) => {
            const existingEntry = prev.byId[proposal.fileId] ?? createFileEntry({
                fileId: proposal.fileId,
                fileName: proposal.fileName,
                previewTexts: prev.byId[proposal.fileId]?.previewTexts ?? "",
            });
            const updatedChunks = existingEntry.contentState.chunks.map((chunk) => {
                if (chunk.parentId !== proposal.parentId) return chunk;
                const patched =
                    chunk.content.slice(0, offset) +
                    proposal.proposed +
                    chunk.content.slice(offset + proposal.original.length);
                return { ...chunk, content: patched, size: patched.length };
            });
            return {
                ...prev,
                openTabIds: prev.openTabIds.includes(proposal.fileId)
                    ? prev.openTabIds
                    : [...prev.openTabIds, proposal.fileId],
                activeFileId: proposal.fileId,
                byId: {
                    ...prev.byId,
                    [proposal.fileId]: {
                        ...existingEntry,
                        contentState: {
                            ...existingEntry.contentState,
                            chunks: updatedChunks,
                        },
                    },
                },
            };
        });
        setChunkAsyncByFileId((prev) => ({
            ...prev,
            [proposal.fileId]: {
                ...(prev[proposal.fileId] ?? createEmptyContentAsyncState()),
                isLoading: false,
                isInitialized: true,
                error: null,
            },
        }));
    }, [getContentStateById, loadFileChunks]);

    // Save: write accepted proposal to DB.
    const saveAgentProposal = useCallback(
        async (proposal: AgentProposal): Promise<boolean> => {
            setSaveError(null);
            setAgentSavingIds((prev) => new Set([...prev, proposal.parentId]));

            try {
                const existingChunk = getContentStateById(proposal.fileId).chunks.find(
                    (c) => c.parentId === proposal.parentId
                );

                // Chunk must be loaded — without full content we cannot safely write to DB
                if (!existingChunk) {
                    setSaveError(`Cannot save: open "${proposal.fileName}" and load its content first.`);
                    return false;
                }

                const batchResp = await axios.post<BatchUpdateParentChunksResponse>(
                    `${API_BASE}/api/modifications/parent-chunks/batch-update`,
                    {
                        fileId: proposal.fileId,
                        fileName: proposal.fileName,
                        mode: "fast_updates",
                        updates: [{ parentId: proposal.parentId, content: existingChunk.content }],
                    }
                );

                const updatedRows = batchResp.data.results ?? [];
                if (updatedRows.length === 0) {
                    setSaveError(`Failed to save changes for ${proposal.fileName}. Please try again.`);
                    return false;
                }

                const resp = { data: updatedRows[0] };

                const newParentId = resp.data.parentId;
                const oldParentId = resp.data.previousParentId;

                // Migrate acceptedMap: old→new for saved proposal, clear others for this file
                // (backend re-splits the entire file, making all other parentIds stale)
                setAgentAcceptedMap((prev) => {
                    const next = new Map(prev);
                    const old = next.get(oldParentId);
                    if (old) {
                        next.delete(oldParentId);
                        next.set(newParentId, { ...old, parentId: newParentId });
                    }
                    for (const [k, v] of next.entries()) {
                        if (v.fileId === proposal.fileId && v.parentId !== newParentId) next.delete(k);
                    }
                    return next;
                });

                // Migrate savedIds
                setAgentSavedIds((prev) => {
                    const next = new Set(prev);
                    next.delete(oldParentId);
                    next.add(newParentId);
                    return next;
                });

                // Migrate rejectedIds (old chunk gone; don't auto-add new as rejected)
                setAgentRejectedIds((prev) => {
                    const next = new Set(prev);
                    next.delete(oldParentId);
                    return next;
                });

                // Fix 2: single setAgentProposals call — migrate old→new then filter stale same-file proposals.
                // Fix 3: also collect stale parentIds to purge from saved/rejected sets.
                setAgentProposals((prev) => {
                    const migrated = prev.map((p) =>
                        p.parentId === oldParentId ? { ...p, parentId: newParentId } : p
                    );
                    const staleIds = migrated
                        .filter((p) => p.fileId === proposal.fileId && p.parentId !== newParentId)
                        .map((p) => p.parentId);

                    if (staleIds.length > 0) {
                        setAgentSavedIds((prev) => {
                            const next = new Set(prev);
                            staleIds.forEach((id) => next.delete(id));
                            return next;
                        });
                        setAgentRejectedIds((prev) => {
                            const next = new Set(prev);
                            staleIds.forEach((id) => next.delete(id));
                            return next;
                        });
                    }

                    return migrated.filter((p) => p.fileId !== proposal.fileId || p.parentId === newParentId);
                });

                // Clear savingIds for both old and new parentId
                setAgentSavingIds((prev) => {
                    const next = new Set(prev);
                    next.delete(oldParentId);
                    next.delete(newParentId);
                    return next;
                });

                await fetchFiles();
                await loadFileChunks(proposal.fileId, true);

                return true;
            } catch {
                setSaveError(`Failed to save changes for ${proposal.fileName}. Please try again.`);
                return false;
            } finally {
                setAgentSavingIds((prev) => {
                    const next = new Set(prev);
                    next.delete(proposal.parentId);
                    return next;
                });
            }
        },
        [fetchFiles, getContentStateById, loadFileChunks]
    );

    const rejectAgentProposal = useCallback((parentId: string) => {
        setAgentProposals((prev) => {
            const proposal = prev.find((p) => p.parentId === parentId);
            if (proposal) {
                setFilesState((filesPrev) => {
                    const existingEntry = filesPrev.byId[proposal.fileId] ?? createFileEntry({
                        fileId: proposal.fileId,
                        fileName: proposal.fileName,
                        previewTexts: filesPrev.byId[proposal.fileId]?.previewTexts ?? "",
                    });
                    const reverted = existingEntry.contentState.chunks.map((chunk) => {
                        if (chunk.parentId !== parentId) return chunk;

                        // F01: use the offset recorded at accept time for a positionally
                        // exact revert. Without it, .replace(proposed, original) would
                        // silently restore the wrong occurrence if `proposed` text appears
                        // elsewhere in the chunk.
                        const acceptedEntry = agentAcceptedMapRef.current.get(parentId);
                        const offset = acceptedEntry?.patchOffset;

                        if (offset !== undefined) {
                            const proposedEnd = offset + proposal.proposed.length;
                            // Verify the proposed text is still at the recorded position
                            // (it should be, unless some other edit moved it).
                            if (chunk.content.slice(offset, proposedEnd) === proposal.proposed) {
                                const restored =
                                    chunk.content.slice(0, offset) +
                                    proposal.original +
                                    chunk.content.slice(proposedEnd);
                                return { ...chunk, content: restored, size: restored.length };
                            }
                            // Position check failed — fall through to indexOf fallback.
                            console.warn(
                                `[F01] Expected "${proposal.proposed.slice(0, 40)}…" at offset ${offset} ` +
                                `but found "${chunk.content.slice(offset, offset + 40)}…". ` +
                                `Falling back to indexOf.`
                            );
                        }

                        // Fallback: find by indexOf (covers proposals accepted before this
                        // fix was deployed, or edge cases where offset is unavailable).
                        const pos = chunk.content.indexOf(proposal.proposed);
                        if (pos === -1) return chunk;
                        const restored =
                            chunk.content.slice(0, pos) +
                            proposal.original +
                            chunk.content.slice(pos + proposal.proposed.length);
                        return { ...chunk, content: restored, size: restored.length };
                    });
                    return {
                        ...filesPrev,
                        byId: {
                            ...filesPrev.byId,
                            [proposal.fileId]: {
                                ...existingEntry,
                                contentState: {
                                    ...existingEntry.contentState,
                                    chunks: reverted,
                                },
                            },
                        },
                    };
                });
            }
            return prev;
        });
        setAgentAcceptedMap((prev) => {
            const next = new Map(prev);
            next.delete(parentId);
            return next;
        });
        setAgentRejectedIds((prev) => new Set([...prev, parentId]));
    }, []); // agentAcceptedMapRef is a stable ref — no dependency needed

    return {
        files,
        filesState,
        chunkAsyncByFileId,
        isLoadingFiles,
        fileListError,
        deletingFileId,
        openTabs,
        activeTab,
        activeTabData,
        activeTabAsync,
        fetchFiles,
        handleRefreshDocuments,
        invalidateDocumentCache,
        deleteFile,
        openDocumentTab,
        closeDocumentTab,
        setActiveDocumentTab,
        loadMoreActiveTab,
        saveError,
        editingDocumentContent,
        isEditingActiveDocument,
        isSavingActiveDocument,
        isActiveDocumentDirty,
        startEditingActiveDocument,
        setActiveEditingDocumentContent,
        cancelEditingActiveDocument,
        saveEditingActiveDocument,
        getFileNameById,
        getFileIdByName,
        isAgentGenerating,
        agentProposals,
        agentAcceptedMap,
        agentSavedIds,
        agentRejectedIds,
        agentSavingIds,
        agentError,
        agentIntention,
        requestAgentEditPreview,
        requestSelectionEditPreview,
        acceptAgentProposal,
        saveAgentProposal,
        rejectAgentProposal,
        clearAgentState,
    };
}
