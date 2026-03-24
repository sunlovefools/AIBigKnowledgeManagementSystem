import { useMemo, useState, type RefObject } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { ChatMessage, ChatProgressStep } from "../types";

type ChatAreaProps = {
    messages: ChatMessage[];
    isUploading: boolean;
    bottomRef: RefObject<HTMLDivElement | null>;
    emptyStateMode?: "welcome" | "no-document";
    showLoadOlderMessages?: boolean;
    isLoadingOlderMessages?: boolean;
    onLoadOlderMessages?: () => void;
};

function renderStepLabel(step: ChatProgressStep): string {
    const batchPrefix = typeof step.batchId === "number" ? `B${step.batchId} ` : "";
    const detail = String(step.message || "").trim() || "Working...";
    return `${batchPrefix}${detail}`;
}

function getProgressTitle(scope: "agentic" | "selection", status: "running" | "completed" | "failed"): string {
    if (status === "completed") return "Completed";
    if (status === "failed") return "Failed";
    return scope === "selection" ? "Selection edit in progress" : "Agent is working";
}

export default function ChatArea({
    messages,
    isUploading,
    bottomRef,
    emptyStateMode = "welcome",
    showLoadOlderMessages = false,
    isLoadingOlderMessages = false,
    onLoadOlderMessages,
}: ChatAreaProps) {
    const [expandedHistoryByMessageId, setExpandedHistoryByMessageId] = useState<Record<string, boolean>>({});

    const toggleHistory = (messageId: string) => {
        setExpandedHistoryByMessageId((prev) => ({ ...prev, [messageId]: !prev[messageId] }));
    };

    const hasMessages = messages.length > 0;

    const progressHistoryCounts = useMemo(() => {
        const counts: Record<string, number> = {};
        for (const message of messages) {
            if (message.kind !== "progress") continue;
            counts[message.id] = Math.max(0, message.steps.length - 1);
        }
        return counts;
    }, [messages]);

    return (
        <div className="chat-scroll-area">
            {!hasMessages ? (
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
                    {showLoadOlderMessages && onLoadOlderMessages && (
                        <div className="load-older-messages-row">
                            <button
                                type="button"
                                className="load-older-messages-btn"
                                onClick={onLoadOlderMessages}
                                disabled={isLoadingOlderMessages}
                            >
                                {isLoadingOlderMessages ? "Loading older messages..." : "Load older messages"}
                            </button>
                        </div>
                    )}

                    {messages.map((msg) => {
                        if (msg.kind === "text") {
                            return (
                                <div key={msg.id} className={`message ${msg.role}`}>
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
                            );
                        }

                        const historyCount = progressHistoryCounts[msg.id] ?? 0;
                        const isExpanded = expandedHistoryByMessageId[msg.id] ?? false;
                        const historicalSteps = historyCount > 0 ? msg.steps.slice(0, -1) : [];

                        return (
                            <div key={msg.id} className="message ai">
                                <div className="message-avatar">AI</div>
                                <div className={`message-content progress-card ${msg.status}`}>
                                    <div className="progress-card-header">
                                        <div className="progress-card-title">{getProgressTitle(msg.scope, msg.status)}</div>
                                    </div>

                                    <div className="progress-current-row">
                                        <span className={`progress-live-indicator ${msg.status}`} aria-hidden="true">
                                            <span />
                                            <span />
                                            <span />
                                        </span>
                                        <span className="progress-current-text">{msg.currentStageText}</span>
                                        {historyCount > 0 && (
                                            <button
                                                type="button"
                                                className={`progress-history-toggle ${isExpanded ? "expanded" : ""}`}
                                                onClick={() => toggleHistory(msg.id)}
                                                aria-label={isExpanded ? `Collapse steps (${historyCount})` : `Expand steps (${historyCount})`}
                                                title={isExpanded ? "Collapse steps" : "Expand steps"}
                                            >
                                                <span className="progress-chevron" aria-hidden="true" />
                                            </button>
                                        )}
                                    </div>

                                    {historyCount > 0 && isExpanded && (
                                        <ul className="progress-step-list">
                                            {historicalSteps.map((step, index) => (
                                                <li key={`${msg.id}-step-${index}`} className="progress-step-item">
                                                    {renderStepLabel(step)}
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                </div>
                            </div>
                        );
                    })}

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
