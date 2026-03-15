/*
  Type definitions for the main page of the frontend application.
*/

// Type for a chat message, which can be from the user or the AI.
export type ChatMessage = {
    role: "user" | "ai";
    text: string;
};

// Type for a sidebar file item (merged by uploaded filename).
export type SidebarFileSummary = {
    fileId: string;
    fileName: string;
    previewTexts: string;
};

// Type for one parent chunk payload in full-view mode.
export type ParentChunkContent = {
    parentId: string;
    content: string;
    size: number;
};

export type HighlightedSelection = {
    fileId: string;
    fileName: string;
    parentId: string;
    selectedText: string;
    startOffset: number;
    endOffset: number;
};

// Type for tab state of an opened file.
export type FileTabState = {
    chunks: ParentChunkContent[];
    hasMore: boolean;
    nextCursor: string | null;
    isLoading: boolean;
    isInitialized: boolean;
    error: string | null;
};

// Type for a single diff segment in a unified diff display.
export type DiffSegment = {
    type: "equal" | "add" | "del";
    text: string;
};

// Type for AI edit proposal with diff information.
export type AiEditProposal = {
    instruction: string;
    originalContent: string;
    editedContent: string;
    summary: string;
    warnings: string[];
    diffSegments: DiffSegment[];
};

// Type for a single agent modification proposal (multi-file agent flow).
export type AgentProposal = {
    fileId: string;
    fileName: string;
    parentId: string;
    original: string;
    proposed: string;
    source?: "agent" | "selection";
    selectionStart?: number;
    selectionEnd?: number;
    /**
     * Byte offset of `original` within the chunk content at accept time.
     * Set by acceptAgentProposal and read by rejectAgentProposal for precise
     * positional revert (B01/F01 fix). Undefined on proposals received from
     * the backend before they have been accepted locally.
     */
    patchOffset?: number;
};
