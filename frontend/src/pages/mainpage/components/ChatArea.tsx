import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { RefObject } from "react";
import type { ChatMessage } from "../types";

// Type definitions for the ChatArea component props
type ChatAreaProps = {
    messages: ChatMessage[]; // An array of chat messages to be displayed in the chat area
    isUploading: boolean;
    processingStatusText?: string | null;
    bottomRef: RefObject<HTMLDivElement | null>;
    emptyStateMode?: "welcome" | "no-document";
};

export default function ChatArea({
    messages,
    isUploading,
    processingStatusText,
    bottomRef,
    emptyStateMode = "welcome",
}: ChatAreaProps) {
    return (
        <div className="chat-scroll-area">
            {!messages.length ? (
                <div className="welcome-screen">
                    {emptyStateMode === "no-document" ? (
                        <>
                            <h2>No document selected</h2>
                            <p>Upload or choose a file to begin editing</p>
                        </>
                    ) : (
                        <>
                            <div className="welcome-icon" aria-hidden="true">
                                <span className="welcome-icon-orbit" />
                                <span className="welcome-icon-core" />
                            </div>
                            <h2>Start the conversation</h2>
                            <p>Upload a document from the left panel, then ask anything about it.</p>
                        </>
                    )}
                </div>
            ) : (
                <div className="messages-container">
                    {messages.map((msg, idx) => (
                        <div key={idx} className={`message ${msg.role}`}>
                            <div className="message-avatar">
                                {msg.role === "user" ? "You" : "AI"}
                            </div>
                            <div className="message-content">
                                <ReactMarkdown
                                    remarkPlugins={[remarkGfm]}
                                    rehypePlugins={[rehypeHighlight]}
                                >
                                    {msg.text}
                                </ReactMarkdown>
                            </div>
                        </div>
                    ))}
                    {/* If the user is uploading the file, the chatbox will show the status of the upload of the file (Maybe can be removed) */}
                    {isUploading && (
                        <div className="message ai">
                            <div className="message-avatar">AI</div>
                            <div className="message-content">
                                <span className="typing-indicator">Reading document...</span>
                            </div>
                        </div>
                    )}
                    {/* Backend-driven progress indicator shown only while edit request is active. */}
                    {processingStatusText && (
                        <div className="message ai">
                            <div className="message-avatar">AI</div>
                            <div className="message-content">
                                <span className="typing-indicator">{processingStatusText}</span>
                            </div>
                        </div>
                    )}
                    <div ref={bottomRef} />
                </div>
            )}
        </div>
    );
}
