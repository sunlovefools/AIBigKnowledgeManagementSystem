import type {
    FileContentAsyncState,
    FileContentState,
    FileEntry,
    FilesState,
    SidebarFileSummary,
} from "../../../types";

// Creates the default chunk container for a file before content is loaded.
export function createEmptyContentState(): FileContentState {
    return {
        chunks: [],
        hasMore: true,
        nextCursor: null,
    };
}

// Creates the default async flags for per-file chunk loading.
export function createEmptyContentAsyncState(): FileContentAsyncState {
    return {
        isLoading: false,
        isInitialized: false,
        error: null,
    };
}

// Builds a normalized file record from sidebar metadata.
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

// Creates the root state container used by the document hooks.
export function createEmptyFilesState(): FilesState {
    return {
        byId: {},
        sidebarFileIds: [],
        openTabIds: [],
        activeFileId: null,
    };
}

// Converts an internal file record back into sidebar shape.
export function toSidebarFileSummary(entry: FileEntry): SidebarFileSummary {
    return {
        fileId: entry.fileId,
        fileName: entry.fileName,
        previewTexts: entry.previewTexts,
    };
}
