import type {
    AgentProposal,
    FileContentAsyncState,
    FileEntry,
    FilesState,
    ParentChunkContent,
    SidebarFileSummary,
} from "../../../types";
import { buildPreviewText } from "../utils/chunkText";
import { createEmptyContentAsyncState, createEmptyContentState, createFileEntry } from "./factories";

// Rebuilds file index from fresh sidebar data while preserving local open tabs/content when possible.
export function replaceFilesFromSidebarSummaries(
    prev: FilesState,
    incoming: SidebarFileSummary[]
): FilesState {
    const validIds = new Set(incoming.map((f) => f.fileId));
    const nextById: Record<string, FileEntry> = {};

    incoming.forEach((summary) => {
        nextById[summary.fileId] = createFileEntry(
            summary,
            prev.byId[summary.fileId]?.contentState ?? createEmptyContentState()
        );
    });

    return {
        byId: nextById,
        sidebarFileIds: incoming.map((file) => file.fileId),
        openTabIds: prev.openTabIds.filter((id) => validIds.has(id)),
        activeFileId: prev.activeFileId && validIds.has(prev.activeFileId) ? prev.activeFileId : null,
    };
}

// Keeps async loading flags aligned with the current sidebar file set.
export function syncChunkAsyncIndex(
    prev: Record<string, FileContentAsyncState>,
    incoming: SidebarFileSummary[]
): Record<string, FileContentAsyncState> {
    const next: Record<string, FileContentAsyncState> = {};
    incoming.forEach((summary) => {
        next[summary.fileId] = prev[summary.fileId] ?? createEmptyContentAsyncState();
    });
    return next;
}

// Removes one file from sidebar + tab state and chooses a sensible next active tab.
export function removeFileFromState(prev: FilesState, fileId: string): FilesState {
    const nextById = { ...prev.byId };
    delete nextById[fileId];
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
}

// Opens (or focuses) a tab and makes it active.
export function openTabState(prev: FilesState, fileId: string): FilesState {
    return {
        ...prev,
        openTabIds: prev.openTabIds.includes(fileId) ? prev.openTabIds : [...prev.openTabIds, fileId],
        activeFileId: fileId,
    };
}

// Closes a tab and shifts focus to the nearest neighbor if needed.
export function closeTabState(prev: FilesState, fileId: string): FilesState {
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
}

// Marks a file as loading and optionally clears current chunk cache for a hard reload.
export function markFileChunkLoading(
    prev: FilesState,
    fileId: string,
    reset: boolean
): FilesState {
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
                    ...(reset ? { chunks: [], nextCursor: null, hasMore: true } : {}),
                },
            },
        },
    };
}

// Writes chunk payload results into the file content state.
export function patchChunkContent(
    prev: FilesState,
    fileId: string,
    chunks: ParentChunkContent[],
    hasMore: boolean,
    nextCursor: string | null
): FilesState {
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
                    chunks,
                    hasMore,
                    nextCursor,
                },
            },
        },
    };
}

// Remaps local state after full-file save when file IDs change on the backend.
export function remapAfterFullFileUpdate(
    prev: FilesState,
    updated: {
        fileId: string;
        previousFileId: string;
        fileName: string;
        content: string;
        size: number;
    },
    previousChunks: ParentChunkContent[]
): FilesState {
    const localParentId = previousChunks[0]?.parentId ?? `local-${updated.fileId}`;
    const refreshedEntry = createFileEntry(
        {
            fileId: updated.fileId,
            fileName: updated.fileName,
            previewTexts: buildPreviewText(updated.content),
        },
        {
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

    const nextById = { ...prev.byId };
    delete nextById[updated.previousFileId];
    nextById[updated.fileId] = refreshedEntry;
    return {
        byId: nextById,
        sidebarFileIds: prev.sidebarFileIds.map((id) => (id === updated.previousFileId ? updated.fileId : id)),
        openTabIds: prev.openTabIds.map((id) => (id === updated.previousFileId ? updated.fileId : id)),
        activeFileId: prev.activeFileId === updated.previousFileId ? updated.fileId : prev.activeFileId,
    };
}

// Optimistically updates just the fileName field for a given fileId.
// Used by the rename flow so the sidebar reflects the new name instantly
// without waiting for the backend round-trip.
export function patchFileName(prev: FilesState, fileId: string, newFileName: string): FilesState {
    const entry = prev.byId[fileId];
    if (!entry) return prev;
    return {
        ...prev,
        byId: {
            ...prev.byId,
            [fileId]: { ...entry, fileName: newFileName },
        },
    };
}

// Replaces visible content immediately after the backend accepts a background save job.
export function patchFileContentOptimistically(
    prev: FilesState,
    fileId: string,
    fileName: string,
    content: string,
    parentId: string,
): FilesState {
    const entry = prev.byId[fileId];
    if (!entry) return prev;
    return {
        ...prev,
        byId: {
            ...prev.byId,
            [fileId]: {
                ...entry,
                fileName,
                previewTexts: buildPreviewText(content),
                contentState: {
                    chunks: [{
                        parentId,
                        content,
                        size: content.length,
                        pageNumbers: [0],
                    }],
                    hasMore: false,
                    nextCursor: null,
                },
            },
        },
    };
}

// Restores the last backend-confirmed content while a failed optimistic save remains available as a draft.
export function restoreFileContentState(
    prev: FilesState,
    fileId: string,
    fileName: string,
    contentState: FileEntry["contentState"],
): FilesState {
    const entry = prev.byId[fileId];
    if (!entry) return prev;
    const mergedContent = contentState.chunks.map((chunk) => chunk.content).join("\n\n");
    return {
        ...prev,
        byId: {
            ...prev.byId,
            [fileId]: {
                ...entry,
                fileName,
                previewTexts: buildPreviewText(mergedContent),
                contentState,
            },
        },
    };
}

// Replaces a temporary (optimistic) file ID with the real ID returned by the backend.
// Updates every FilesState map that holds the ID, and replaces the synthetic parentId
// inside the chunk list so that save operations find the correct DB record.
export function swapTempFileId(
    prev: FilesState,
    tempId: string,
    realId: string,
    realParentId: string,
): FilesState {
    const tempEntry = prev.byId[tempId];
    if (!tempEntry) return prev;

    const realEntry: FileEntry = {
        ...tempEntry,
        fileId: realId,
        contentState: {
            ...tempEntry.contentState,
            chunks: tempEntry.contentState.chunks.map((c) =>
                c.parentId === tempId || c.parentId.startsWith("tmp-")
                    ? { ...c, parentId: realParentId }
                    : c
            ),
        },
    };

    const nextById = { ...prev.byId };
    delete nextById[tempId];
    nextById[realId] = realEntry;

    return {
        byId: nextById,
        sidebarFileIds: prev.sidebarFileIds.map((id) => (id === tempId ? realId : id)),
        openTabIds: prev.openTabIds.map((id) => (id === tempId ? realId : id)),
        activeFileId: prev.activeFileId === tempId ? realId : prev.activeFileId,
    };
}
export function remapAcceptedAgentOffsets(
    prev: Map<string, AgentProposal>,
    targetParentId: string,
    targetFileId: string,
    pivotOffset: number,
    delta: number
): Map<string, AgentProposal> {
    const next = new Map(prev);
    for (const [parentId, entry] of next.entries()) {
        if (parentId === targetParentId) continue;
        if (entry.fileId !== targetFileId || typeof entry.patchOffset !== "number") continue;
        if (entry.patchOffset > pivotOffset) {
            next.set(parentId, { ...entry, patchOffset: entry.patchOffset + delta });
        }
    }
    return next;
}
