/*
  Type definitions for the main page of the frontend application.
*/

// Enhanced type for a chat message with metadata for chat history feature.
export type ChatMessage = {
    messageId?: string; // Unique ID from backend
    userEmail?: string; // Email of the user who initiated the conversation
    role: "user" | "ai";
    text: string;
    timestamp?: string; // ISO timestamp when message was created
    sources?: string[]; // Array of source documents that informed the AI response
};

// Type for a conversation - groups related messages
export type Conversation = {
    conversationId: string;
    userEmail: string; // Using User's email as the userID
    title: string; // Auto-generated or user-set title
    messages: ChatMessage[];
    createdAt: string; // ISO timestamp
    updatedAt: string; // ISO timestamp
};

// Lightweight type for conversation list display
export type ConversationSummary = {
    conversationId: string;
    title: string;
    updatedAt: string;
    messageCount?: number;
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

// Type for tab state of an opened file.
export type FileTabState = {
    chunks: ParentChunkContent[];
    hasMore: boolean;
    nextCursor: string | null;
    isLoading: boolean;
    isInitialized: boolean;
    error: string | null;
};
