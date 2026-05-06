import { useMemo, useState, type RefObject } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { ChatMessage, ChatProgressSnapshot, ChatProgressStep, ChatProgressTranscriptItem } from "../types";

type ChatAreaProps = {
    messages: ChatMessage[];
    bottomRef: RefObject<HTMLDivElement | null>;
    emptyStateMode?: "welcome" | "no-document";
};

function renderStepLabel(step: ChatProgressStep): string {
    const batchPrefix = typeof step.batchId === "number" ? `B${step.batchId} ` : "";
    const stepPrefix = typeof step.step === "number" ? `S${step.step} ` : "";
    const toolPrefix = step.tool ? `[${step.tool}] ` : "";
    const detail = String(step.message || "").trim() || "Working...";
    return `${batchPrefix}${stepPrefix}${toolPrefix}${detail}`;
}

function renderStepDetails(step: ChatProgressStep, scope: "agentic" | "selection" | "agentic-search" | "standard-search"): string[] {
    const details: string[] = [];
    if (step.intent) {
        details.push(`Intent: ${step.intent}`);
    }
    if (step.successCriteria) {
        details.push(`Goal: ${step.successCriteria}`);
    }
    if (step.fallback || step.decision) {
        details.push(`Next if needed: ${step.fallback || step.decision}`);
    }
    if (step.argumentsPreview && scope !== "agentic-search") {
        details.push(`Args: ${step.argumentsPreview}`);
    }
    if (step.observation) {
        details.push(`Observation: ${step.observation}`);
    }
    if (step.decision && !step.fallback) {
        details.push(`Decision: ${step.decision}`);
    }
    return details;
}

function getTranscriptRoleLabel(role: ChatProgressTranscriptItem["role"]): string {
    if (role === "system") return "Setup";
    if (role === "tool") return "Result";
    return "Assistant";
}

function getTranscriptTitle(message: ChatProgressTranscriptItem): string {
    const label = getTranscriptRoleLabel(message.role);
    return `${label}: ${message.title}`;
}

function getProgressTitle(
    scope: "agentic" | "selection" | "agentic-search" | "standard-search",
    status: "running" | "completed" | "failed"
): string {
    if (status === "completed") return "Completed";
    if (status === "failed") return "Failed";
    if (scope === "standard-search") return "Standard search in progress";
    if (scope === "agentic-search") return "Agentic search in progress";
    return scope === "selection" ? "Selection edit in progress" : "Agent is working";
}

function getScopeLabel(message: ChatMessage): string | null {
    if (message.kind !== "text" || !message.searchScope) return null;
    const verb = message.role === "user" ? "Asking" : "Searched";
    if (message.searchScope === "all_collections") return `${verb} all collections`;
    return `${verb} ${message.collectionName || "selected collection"}`;
}

function getProgressTraceLabel(trace: ChatProgressSnapshot): string {
    if (trace.status === "failed") return "Agentic search process failed";
    if (trace.scope === "standard-search") return "Search process";
    return "Agentic search process";
}

function renderProgressTranscriptItems(
    messageId: string,
    transcriptItems: ChatProgressTranscriptItem[]
) {
    if (transcriptItems.length === 0) return null;

    return (
        <div className="progress-transcript" aria-label="Agent activity">
            {transcriptItems.map((item, index) => (
                <div
                    key={`${messageId}-transcript-${index}`}
                    className={`progress-transcript-item ${item.role} ${item.status || "running"}`}
                >
                    <div className="progress-transcript-title">
                        {getTranscriptTitle(item)}
                    </div>
                    <div className="progress-transcript-summary">
                        {item.summary}
                    </div>
                    {item.detail && (
                        <div className="progress-transcript-detail">
                            {item.detail}
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}

function renderProgressStepList(
    messageId: string,
    steps: ChatProgressStep[],
    scope: ChatProgressSnapshot["scope"]
) {
    if (steps.length === 0) return null;

    return (
        <ul className="progress-step-list">
            {steps.map((step, index) => {
                const detailRows = renderStepDetails(step, scope);
                return (
                    <li key={`${messageId}-step-${index}`} className="progress-step-item">
                        <div className="progress-step-primary">
                            {renderStepLabel(step)}
                        </div>
                        {detailRows.length > 0 && (
                            <div className="progress-step-details">
                                {detailRows.map((row, rowIndex) => (
                                    <div key={`${messageId}-step-${index}-detail-${rowIndex}`}>{row}</div>
                                ))}
                            </div>
                        )}
                    </li>
                );
            })}
        </ul>
    );
}

export default function ChatArea({
    messages,
    bottomRef,
    emptyStateMode = "welcome",
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
        <div className={`chat-scroll-area ${hasMessages ? "has-messages" : "empty"}`}>
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
                            <p>Ask across all collections first, or narrow the next question with the search scope.</p>
                        </>
                    )}
                </div>
            ) : (
                <div className="messages-container">
                    {messages.map((msg) => {
                        if (msg.kind === "text") {
                            const scopeLabel = getScopeLabel(msg);
                            const progressTrace = msg.progressTrace;
                            const isTraceExpanded = expandedHistoryByMessageId[msg.id] ?? false;
                            const traceStepCount = progressTrace?.steps.length ?? 0;
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
                                        {progressTrace && (
                                            <div className={`message-progress-trace ${progressTrace.status}`}>
                                                <button
                                                    type="button"
                                                    className={`message-progress-toggle ${isTraceExpanded ? "expanded" : ""}`}
                                                    onClick={() => toggleHistory(msg.id)}
                                                    aria-expanded={isTraceExpanded}
                                                    aria-label={isTraceExpanded ? "Collapse search process" : "Expand search process"}
                                                    title={isTraceExpanded ? "Collapse search process" : "Expand search process"}
                                                >
                                                    <span className="progress-chevron" aria-hidden="true" />
                                                    <span>{getProgressTraceLabel(progressTrace)}</span>
                                                    {traceStepCount > 0 && (
                                                        <span className="message-progress-count">{traceStepCount} step(s)</span>
                                                    )}
                                                </button>
                                                {isTraceExpanded && (
                                                    <div className="message-progress-panel">
                                                        <div className="message-progress-current">
                                                            {progressTrace.currentStageText}
                                                        </div>
                                                        {renderProgressTranscriptItems(msg.id, progressTrace.transcript)}
                                                        {renderProgressStepList(msg.id, progressTrace.steps, progressTrace.scope)}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                        {scopeLabel && <div className="message-scope-label">{scopeLabel}</div>}
                                    </div>
                                </div>
                            );
                        }

                        const historyCount = progressHistoryCounts[msg.id] ?? 0;
                        const isExpanded = expandedHistoryByMessageId[msg.id] ?? false;
                        const historicalSteps = historyCount > 0 ? msg.steps.slice(0, -1) : [];
                        const transcriptItems = msg.transcript ?? [];

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

                                    {renderProgressTranscriptItems(msg.id, transcriptItems)}

                                    {historyCount > 0 && isExpanded && (
                                        renderProgressStepList(msg.id, historicalSteps, msg.scope)
                                    )}
                                </div>
                            </div>
                        );
                    })}

                    <div ref={bottomRef} />
                </div>
            )}
        </div>
    );
}
