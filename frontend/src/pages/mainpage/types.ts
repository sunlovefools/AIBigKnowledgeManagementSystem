/*
  Type definitions for the main page of the frontend application.
*/

export type ChatRole = "user" | "ai";

// Normal conversational chat message.
export type ChatTextMessage = {
    id: string;
    kind: "text";
    role: ChatRole;
    messageId?: string; // Unique ID from backend
    userEmail?: string; // Email of the user who initiated the conversation
    text: string;
    timestamp?: string; // ISO timestamp when message was created
    sources?: string[]; // Array of source documents that informed the AI response
};

// Type for a conversation - groups related messages
export type Conversation = {
    conversationId: string;
    userEmail: string; // Using User's email as the userID
    title: string; // Auto-generated or user-set title
    messages: ChatTextMessage[];
    createdAt: string; // ISO timestamp
    updatedAt: string; // ISO timestamp
};

// Lightweight type for conversation list display, contains summary info
export type ConversationSummary = {
    conversationId: string;
    title: string;
    updatedAt: string;
    messageCount?: number;
    lastMessage?: {
        role: "user" | "ai";
        text: string;
        timestamp: string;
    };
};

// One backend-emitted progress step for an in-flight edit call.
export type ChatProgressStep = {
    stage: string;
    message: string;
    batchId?: number;
    step?: number;
    tool?: string;
    intent?: string;
    decision?: string;
    observation?: string;
    argumentsPreview?: string;
};

// Progress timeline card that remains in chat history per call.
export type ChatProgressMessage = {
    id: string;
    kind: "progress";
    role: "ai";
    status: "running" | "completed" | "failed";
    scope: "agentic" | "selection" | "agentic-search";
    currentStageText: string;
    steps: ChatProgressStep[];
};

// Chat message exchanged between the user and AI.
export type ChatMessage = ChatTextMessage | ChatProgressMessage;

// One file item shown in the sidebar.
export type SidebarFileSummary = {
    fileId: string;
    fileName: string;
    previewTexts: string;
};

export type UserCollectionSummary = {
    collectionId: string;
    name: string;
    isDefault: boolean;
    fileCount: number;
    createdAt: string;
    updatedAt: string;
};

// Text selection captured from the active file view.
export type HighlightedSelection = {
    fileId: string;
    fileName: string;
    selectedText: string;
    // Offsets captured from rendered text relative to startChunkNumber.
    startOffset: number;
    endOffset: number;
    // 1-based chunk numbers (inclusive) of the selected rendered window.
    startChunkNumber: number;
    endChunkNumber: number;
};

export type FileContentAsyncState = {
    isLoading: boolean;
    isInitialized: boolean;
    error: string | null;
};

// One parent chunk payload in full-view mode.
export type ParentChunkContent = {
    parentId: string;
    content: string;
    size: number;
    pageNumbers: number[];
};

// Loaded content and pagination state for one file.
export type FileContentState = {
    chunks: ParentChunkContent[];
    hasMore: boolean;
    nextCursor: string | null;
};

// Top-level store for the document workspace.
// byId holds file objects, while ID arrays define UI ordering and active selection.
export type FilesState = {
    byId: Record<string, FileEntry>;
    sidebarFileIds: string[];
    openTabIds: string[];
    activeFileId: string | null;
};

// Unified frontend model for a file shown in the sidebar and document panel.
export type FileEntry = {
    fileId: string;
    fileName: string;
    previewTexts: string;
    contentState: FileContentState;
};

// Backward-compatible alias for places that still read the currently active file's content state as a tab state.
export type FileTabState = FileContentState;
export type FileTabAsyncState = FileContentAsyncState;

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
     * Offset of `proposed` within the current editing draft after acceptance.
     * Used by rejectAgentProposal for positionally exact revert.
     */
    patchOffset?: number;
    /**
     * Baseline offset of `original` in the non-edited document content.
     * Used to estimate draft offsets when multiple proposals are accepted
     * before saving.
     */
    patchBaselineOffset?: number;
};

export type ProposalStatus = "pending" | "accepted" | "rejected";

export type InlineDiffToken = {
    type: "equal" | "add" | "del";
    text: string;
};

export type ResolvedProposalMarker = {
    proposalKey: string;
    parentId: string;
    fileId: string;
    fileName: string;
    offset: number;
    baselineOffset: number;
    sourceLength: number;
    replacementLength: number;
    status: ProposalStatus;
    proposal: AgentProposal;
    tokens: InlineDiffToken[];
};

export type PendingModificationNavItem = {
    fileId: string;
    fileName: string;
    pendingCount: number;
    targetProposalKey: string;
};
