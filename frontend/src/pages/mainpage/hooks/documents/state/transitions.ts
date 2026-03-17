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

export function openTabState(prev: FilesState, fileId: string): FilesState {
    return {
        ...prev,
        openTabIds: prev.openTabIds.includes(fileId) ? prev.openTabIds : [...prev.openTabIds, fileId],
        activeFileId: fileId,
    };
}

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

