import axios from "axios";
import type { HighlightedSelection, ParentChunkContent, SidebarFileSummary, UserCollectionSummary } from "../../../types";
import { apiClient, authenticatedFetch } from "../../../../../auth/apiClient";
import { API_BASE } from "../../../../../config/env";

// Shared chunk pagination size used by the document panel.
export const PAGE_SIZE = 7;

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

export type SaveJobMode = "fast_updates" | "boundary_rechunk" | "full_file";

export type SubmitSaveJobPayload = {
    fileId: string;
    fileName: string;
    content: string;
    mode: SaveJobMode;
    updates?: Array<{ parentId: string; content: string }>;
    touchedParentIds?: string[];
    newFileName?: string;
    expectedContentHash?: string;
};

export type SaveJobAcceptedResponse = {
    jobId: string;
    status: "queued";
    fileId: string;
};

export type SaveJobStatusResponse = {
    jobId: string;
    status: "queued" | "running" | "succeeded" | "failed";
    fileId: string;
    result?: Record<string, unknown> | null;
    error?: string | null;
    submittedAt: string;
    startedAt?: string | null;
    finishedAt?: string | null;
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

export type RenameFileResponse = {
    fileId: string;
    oldFileName: string;
    fileName: string;
    parentChunks: number;
};

export type CreateBlankFileResponse = {
    fileId: string;
    fileName: string;
    content: string;
    parentId: string;
    parentChunks: number;
    chunks: number;
};

export type CreateCollectionResponse = UserCollectionSummary;

export type DeleteCollectionResponse = {
    collectionId: string;
    name: string;
    deletedFiles: number;
    deletedParentChunks: number;
    deletedChildChunks: number;
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

export type ModificationAgentMode = "workflow" | "skills";

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

// Loads sidebar metadata for all files currently available in the knowledge base.
export async function getAllPreviewFiles(collectionId?: string | null): Promise<SidebarFileSummary[]> {
    const response = await apiClient.get(`${API_BASE}/api/retrieve/all-preview-files`, {
        ...(collectionId ? { params: { collectionId } } : {}),
    });
    return (response.data.files ?? []) as SidebarFileSummary[];
}

export async function getCollections(): Promise<UserCollectionSummary[]> {
    const response = await apiClient.get(`${API_BASE}/api/collections`);
    return (response.data.collections ?? []) as UserCollectionSummary[];
}

export async function createCollection(name: string): Promise<CreateCollectionResponse> {
    const response = await apiClient.post<CreateCollectionResponse>(`${API_BASE}/api/collections`, { name });
    return response.data;
}

export async function renameCollection(collectionId: string, newName: string): Promise<UserCollectionSummary> {
    const response = await apiClient.patch<UserCollectionSummary>(`${API_BASE}/api/collections/${collectionId}`, { newName });
    return response.data;
}

export async function deleteCollection(collectionId: string): Promise<DeleteCollectionResponse> {
    const response = await apiClient.delete<DeleteCollectionResponse>(`${API_BASE}/api/collections/${collectionId}`);
    return response.data;
}

// Fetches one page of parent chunks for a file.
export async function getFileChunks(
    fileId: string,
    cursor: string | null,
    collectionId?: string | null,
): Promise<{ chunks: ParentChunkContent[]; hasMore: boolean; nextCursor: string | null }> {
    const response = await apiClient.get(`${API_BASE}/api/retrieve/file-chunks`, {
        params: {
            fileId,
            limit: PAGE_SIZE,
            ...(cursor ? { cursor } : {}),
            ...(collectionId ? { collectionId } : {}),
        },
    });
    return {
        chunks: (response.data.chunks ?? []) as ParentChunkContent[],
        hasMore: Boolean(response.data.hasMore),
        nextCursor: response.data.nextCursor ?? null,
    };
}

// Deletes a file and its indexed data in the backend.
export async function deleteKnowledgeFile(fileId: string): Promise<DeleteFileResponse> {
    const response = await apiClient.delete<DeleteFileResponse>(`${API_BASE}/api/modifications/files/${fileId}`);
    return response.data;
}

// Replaces an entire file body in one operation.
export async function updateFileContent(fileId: string, fileName: string, content: string): Promise<UpdateFileResponse> {
    const response = await apiClient.put<UpdateFileResponse>(
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
    const response = await apiClient.post<BatchUpdateParentChunksResponse>(
        `${API_BASE}/api/modifications/parent-chunks/batch-update`,
        payload
    );
    return response.data;
}

export async function submitSaveJob(payload: SubmitSaveJobPayload): Promise<SaveJobAcceptedResponse> {
    const response = await apiClient.post<SaveJobAcceptedResponse>(
        `${API_BASE}/api/modifications/save-jobs`,
        payload
    );
    return response.data;
}

export async function getSaveJobStatus(jobId: string): Promise<SaveJobStatusResponse> {
    const response = await apiClient.get<SaveJobStatusResponse>(
        `${API_BASE}/api/modifications/save-jobs/${jobId}`
    );
    return response.data;
}

// Requests multi-file edit proposals from the backend agent.
export async function requestAgentModify(
    instruction: string,
    fileIds: string[] | null,
    collectionId: string | null,
    mode: ModificationAgentMode = "workflow",
    onProgress?: (progress: ModificationProgressEvent) => void
): Promise<AgentModifyResponse> {
    // Use fetch instead of axios here because we need direct stream-reader access.
    const endpoint = mode === "skills" ? "modify-skills-stream" : "modify-stream";
    const response = await authenticatedFetch(`${API_BASE}/api/agent/${endpoint}`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        body: JSON.stringify({
            user_instructions: instruction,
            fileIds: fileIds && fileIds.length > 0 ? fileIds : null,
            collectionId: collectionId || null,
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
    const response = await authenticatedFetch(`${API_BASE}/api/modifications/selection-edit-preview-stream`, {
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

// Renames a file in the knowledge base without touching its content.
export async function renameKnowledgeFile(fileId: string, newFileName: string): Promise<RenameFileResponse> {
    const response = await apiClient.patch<RenameFileResponse>(
        `${API_BASE}/api/modifications/rename-file/${fileId}`,
        { newFileName }
    );
    return response.data;
}

// Creates a new blank file in the knowledge base with a given name.
export async function createBlankFile(fileName: string, collectionId?: string | null): Promise<CreateBlankFileResponse> {
    const response = await apiClient.post<CreateBlankFileResponse>(
        `${API_BASE}/api/modifications/create-blank-file`,
        {
            fileName,
            ...(collectionId ? { collectionId } : {}),
        }
    );
    return response.data;
}

// Extracts backend detail text from Axios errors for UI messages.
export function getAxiosErrorDetail(error: unknown): string | null {
    if (!axios.isAxiosError(error)) return null;
    return typeof error.response?.data?.detail === "string" ? error.response.data.detail : null;
}
