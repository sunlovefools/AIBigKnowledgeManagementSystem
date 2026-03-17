import type {
    FileContentAsyncState,
    FileContentState,
    FileEntry,
    FilesState,
    SidebarFileSummary,
} from "../../../types";

export function createEmptyContentState(): FileContentState {
    return {
        chunks: [],
        hasMore: true,
        nextCursor: null,
    };
}

export function createEmptyContentAsyncState(): FileContentAsyncState {
    return {
        isLoading: false,
        isInitialized: false,
        error: null,
    };
}

export function createFileEntry(
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

export function createEmptyFilesState(): FilesState {
    return {
        byId: {},
        sidebarFileIds: [],
        openTabIds: [],
        activeFileId: null,
    };
}

export function toSidebarFileSummary(entry: FileEntry): SidebarFileSummary {
    return {
        fileId: entry.fileId,
        fileName: entry.fileName,
        previewTexts: entry.previewTexts,
    };
}

