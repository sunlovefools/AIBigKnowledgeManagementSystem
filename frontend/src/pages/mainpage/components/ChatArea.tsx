import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { RefObject } from "react";
import type { ChatMessage } from "../types";

type ChatAreaProps = {
    messages: ChatMessage[];
    isUploading: boolean;
    bottomRef: RefObject<HTMLDivElement | null>;
};

export default function ChatArea({ messages, isUploading, bottomRef }: ChatAreaProps) {
    return (
        <div className="chat-scroll-area">
            {!messages.length ? (
                <div className="welcome-screen">
                    <div className="welcome-icon">*</div>
                    <h2>Start the conversation</h2>
                    <p>Upload a document from the left panel, then ask anything about it.</p>
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
                    {isUploading && (
                        <div className="message ai">
                            <div className="message-avatar">AI</div>
                            <div className="message-content">
                                <span className="typing-indicator">Reading document...</span>
                            </div>
                        </div>
                    )}
                    <div ref={bottomRef} />
                </div>
            )}
        </div>
    );
}
