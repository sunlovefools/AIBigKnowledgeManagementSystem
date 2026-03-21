import axios from "axios";
import type { HighlightedSelection, ParentChunkContent, SidebarFileSummary } from "../../../types";

// Normalize trailing slash so endpoint concatenation is predictable.
const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

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

// Loads sidebar metadata for all files currently available in the knowledge base.
export async function getAllPreviewFiles(): Promise<SidebarFileSummary[]> {
    const response = await axios.get(`${API_BASE}/api/retrieve/all-preview-files`);
    return (response.data.files ?? []) as SidebarFileSummary[];
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
    fileIds: string[] | null
): Promise<AgentModifyResponse> {
    const response = await axios.post<AgentModifyResponse>(
        `${API_BASE}/api/agent/v2/modify`,
        { user_instructions: instruction, fileIds: fileIds && fileIds.length > 0 ? fileIds : null }
    );
    return response.data;
}

// Requests a single selection-based rewrite proposal.
export async function requestSelectionPreview(
    instruction: string,
    selection: HighlightedSelection
): Promise<SelectionEditPreviewResponse> {
    const response = await axios.post<SelectionEditPreviewResponse>(
        `${API_BASE}/api/modifications/selection-edit-preview`,
        {
            fileId: selection.fileId,
            fileName: selection.fileName,
            selectedText: selection.selectedText,
            startOffset: selection.startOffset,
            endOffset: selection.endOffset,
            startChunkNumber: selection.startChunkNumber,
            endChunkNumber: selection.endChunkNumber,
            instruction,
        }
    );
    return response.data;
}

// Extracts backend detail text from Axios errors for UI messages.
export function getAxiosErrorDetail(error: unknown): string | null {
    if (!axios.isAxiosError(error)) return null;
    return typeof error.response?.data?.detail === "string" ? error.response.data.detail : null;
}
