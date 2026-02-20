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
    fileName: string;
    preview: string;
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
    totalParentChunks: number;
    error: string | null;
};
