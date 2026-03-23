import axios from "axios";
import type { HighlightedSelection, ParentChunkContent, SidebarFileSummary } from "../../../types";

// Normalize trailing slash so endpoint concatenation is predictable.
const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

// Shared chunk pagination size used by the document panel.
export const PAGE_SIZE = 7;
export const PREVIEW_PAGE_SIZE = 20;

export type UpdateParentChunkResponse = {
    parentId: string;
    previousParentId: string;
    fileName: string;
    content: string;
    size: number;
    chunks: number;
};

export type BatchUpdateParentChunksResponse = {
    fileId: string;
    fileName: string;
    updatedCount: number;
    results: UpdateParentChunkResponse[];
    requiresReload: boolean;
};

export type UpdateFileResponse = {
    fileId: string;
    previousFileId: string;
    fileName: string;
    content: string;
    size: number;
    parentChunks: number;
    chunks: number;
};

export type DeleteFileResponse = {
    fileId: string;
    fileName: string;
    deletedParentChunks: number;
    deletedChildChunks: number;
    s3Status: "deleted" | "not_found" | "skipped" | "failed";
    s3DeletedObjects: number;
    warnings: string[];
};

export type AgentModifyResponse = {
    intention: string;
    proposals: Array<{
        fileId: string;
        fileName: string;
        parentId: string;
        original: string;
        proposed: string;
        source?: "agent" | "selection";
        selectionStart?: number;
        selectionEnd?: number;
    }>;
};

export type SelectionEditPreviewResponse = {
    fileId: string;
    fileName: string;
    selectionId: string;
    selectedText: string;
    proposedText: string;
    startOffset: number;
    endOffset: number;
};

export type ModificationProgressEvent = {
    stage: string;
    status: "started" | "in_progress" | "completed" | "failed" | string;
    message: string;
    timestamp?: string;
    batchId?: number;
    metadata?: Record<string, unknown>;
};

type StreamEvent = {
    event: string;
    data: string;
};

function parseStreamEvent(rawChunk: string): StreamEvent | null {
    // Parse one SSE block (event + one or more data lines).
    const lines = rawChunk
        .split("\n")
        .map((line) => line.trimEnd())
        .filter((line) => line.length > 0 && !line.startsWith(":"));
    if (!lines.length) return null;

    let event = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
        if (line.startsWith("event:")) {
            event = line.slice("event:".length).trim();
            continue;
        }
        if (line.startsWith("data:")) {
            dataLines.push(line.slice("data:".length).trimStart());
        }
    }
    if (!dataLines.length) return null;
    return { event, data: dataLines.join("\n") };
}

async function readJsonStreamResult<T>(
    response: Response,
    onProgress?: (progress: ModificationProgressEvent) => void
): Promise<T> {
    if (!response.ok) {
        const bodyText = await response.text();
        let detail = bodyText || `Request failed with status ${response.status}.`;
        try {
            const parsed = JSON.parse(bodyText) as { detail?: unknown };
            if (typeof parsed.detail === "string" && parsed.detail.trim()) {
                detail = parsed.detail;
            }
        } catch {
            // Ignore JSON parse error and keep text fallback.
        }
        throw new Error(detail);
    }

    if (!response.body) {
        throw new Error("No response stream received from server.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let result: T | null = null;

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        // Buffer chunks until we reach SSE frame boundaries (`\n\n`).
        buffer += decoder.decode(value, { stream: true });
        let boundaryIndex = buffer.indexOf("\n\n");
        while (boundaryIndex !== -1) {
            const rawEvent = buffer.slice(0, boundaryIndex);
            buffer = buffer.slice(boundaryIndex + 2);

            const parsed = parseStreamEvent(rawEvent);
            if (parsed) {
                if (parsed.event === "progress") {
                    // Forward live backend stage updates to the UI callback.
                    try {
                        onProgress?.(JSON.parse(parsed.data) as ModificationProgressEvent);
                    } catch {
                        // Ignore malformed progress frames.
                    }
                } else if (parsed.event === "result") {
                    // Terminal success payload.
                    result = JSON.parse(parsed.data) as T;
                } else if (parsed.event === "error") {
                    // Terminal failure payload.
                    let detail = "Streaming request failed.";
                    try {
                        const errorPayload = JSON.parse(parsed.data) as { detail?: unknown };
                        if (typeof errorPayload.detail === "string" && errorPayload.detail.trim()) {
                            detail = errorPayload.detail;
                        }
                    } catch {
                        detail = parsed.data || detail;
                    }
                    throw new Error(detail);
                }
            }

            boundaryIndex = buffer.indexOf("\n\n");
        }
    }

    if (result === null) {
        throw new Error("Stream ended without a result payload.");
    }
    return result;
}

export type PreviewFilesPageResponse = {
    files: SidebarFileSummary[];
    hasMore: boolean;
    nextCursor: string | null;
    total: number;
};

// Loads one paginated sidebar page from the knowledge base.
export async function getAllPreviewFiles(cursor: string | null): Promise<PreviewFilesPageResponse> {
    const response = await axios.get(`${API_BASE}/api/retrieve/all-preview-files`, {
        params: { limit: PREVIEW_PAGE_SIZE, ...(cursor ? { cursor } : {}) },
    });
    return {
        files: (response.data.files ?? []) as SidebarFileSummary[],
        hasMore: Boolean(response.data.hasMore),
        nextCursor: response.data.nextCursor ?? null,
        total: typeof response.data.total === "number" ? response.data.total : 0,
    };
}

// Fetches one page of parent chunks for a file.
export async function getFileChunks(
    fileId: string,
    cursor: string | null
): Promise<{ chunks: ParentChunkContent[]; hasMore: boolean; nextCursor: string | null }> {
    const response = await axios.get(`${API_BASE}/api/retrieve/file-chunks`, {
        params: { fileId, limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) },
    });
    return {
        chunks: (response.data.chunks ?? []) as ParentChunkContent[],
        hasMore: Boolean(response.data.hasMore),
        nextCursor: response.data.nextCursor ?? null,
    };
}

// Deletes a file and its indexed data in the backend.
export async function deleteKnowledgeFile(fileId: string): Promise<DeleteFileResponse> {
    const response = await axios.delete<DeleteFileResponse>(`${API_BASE}/api/modifications/files/${fileId}`);
    return response.data;
}

// Replaces an entire file body in one operation.
export async function updateFileContent(fileId: string, fileName: string, content: string): Promise<UpdateFileResponse> {
    const response = await axios.put<UpdateFileResponse>(
        `${API_BASE}/api/modifications/update-file/${fileId}`,
        { fileName, content }
    );
    return response.data;
}

// Applies chunk-scoped updates for faster saves when full-file rewrite is not required.
export async function batchUpdateParentChunks(payload: {
    fileId: string;
    fileName: string;
    mode: "boundary_rechunk" | "fast_updates";
    fullContent?: string;
    touchedParentIds?: string[];
    updates?: Array<{ parentId: string; content: string }>;
}): Promise<BatchUpdateParentChunksResponse> {
    const response = await axios.post<BatchUpdateParentChunksResponse>(
        `${API_BASE}/api/modifications/parent-chunks/batch-update`,
        payload
    );
    return response.data;
}

// Requests multi-file edit proposals from the backend agent.
export async function requestAgentModify(
    instruction: string,
    fileIds: string[] | null,
    onProgress?: (progress: ModificationProgressEvent) => void
): Promise<AgentModifyResponse> {
    // Use fetch instead of axios here because we need direct stream-reader access.
    const response = await fetch(`${API_BASE}/api/agent/modify-stream`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        body: JSON.stringify({
            user_instructions: instruction,
            fileIds: fileIds && fileIds.length > 0 ? fileIds : null,
        }),
    });
    return await readJsonStreamResult<AgentModifyResponse>(response, onProgress);
}

// Requests a single selection-based rewrite proposal.
export async function requestSelectionPreview(
    instruction: string,
    selection: HighlightedSelection,
    onProgress?: (progress: ModificationProgressEvent) => void
): Promise<SelectionEditPreviewResponse> {
    // Stream variant exposes real backend stages for highlighted edits.
    const response = await fetch(`${API_BASE}/api/modifications/selection-edit-preview-stream`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        body: JSON.stringify({
            fileId: selection.fileId,
            fileName: selection.fileName,
            selectedText: selection.selectedText,
            startOffset: selection.startOffset,
            endOffset: selection.endOffset,
            startChunkNumber: selection.startChunkNumber,
            endChunkNumber: selection.endChunkNumber,
            instruction,
        }),
    });
    return await readJsonStreamResult<SelectionEditPreviewResponse>(response, onProgress);
}

// Extracts backend detail text from Axios errors for UI messages.
export function getAxiosErrorDetail(error: unknown): string | null {
    if (!axios.isAxiosError(error)) return null;
    return typeof error.response?.data?.detail === "string" ? error.response.data.detail : null;
}
