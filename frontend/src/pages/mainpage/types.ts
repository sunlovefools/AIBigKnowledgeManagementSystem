/*
  Type definitions for the main page of the frontend application.
*/

// Type for a chat message, which can be from the user or the AI.
export type ChatMessage = {
    role: "user" | "ai";
    text: string;
};

// Type for a document item, representing an uploaded document with its metadata.
export type DocumentItem = {
    id: string;
    fileName: string;
    content: string;
    size: number;
    chunks: number;
};
