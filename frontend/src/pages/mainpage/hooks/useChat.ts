import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import axios from "axios";
import type { ChatMessage, ChatProgressMessage, ChatProgressStep, ChatRole } from "../types";
import type { ModificationProgressEvent } from "./documents/api/documentsApi";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

type AppendTextMessagePayload = {
    role: ChatRole;
    text: string;
};

function normalizeProgressStatus(raw: string | undefined): "running" | "completed" | "failed" {
    const normalized = String(raw || "").trim().toLowerCase();
    if (normalized === "failed") return "failed";
    if (normalized === "completed") return "completed";
    return "running";
}

function humanizeStage(stage: string): string {
    const cleaned = String(stage || "").trim();
    if (!cleaned) return "Processing";
    return cleaned
        .replace(/[_-]+/g, " ")
        .replace(/\s+/g, " ")
        .trim()
        .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatCurrentStageText(stage: string, message: string, batchId?: number): string {
    const stageLabel = humanizeStage(stage);
    const detail = String(message || "").trim() || "Working...";
    const prefix = typeof batchId === "number" ? `Batch ${batchId} | ` : "";
    return `${prefix}${stageLabel}: ${detail}`;
}

// Custom hook to manage chat state and interactions.
export function useChat() {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState("");
    const [isQuerying, setIsQuerying] = useState(false);
    const messageCounterRef = useRef(0);

    const nextMessageId = useCallback(() => {
        messageCounterRef.current += 1;
        return `msg-${Date.now()}-${messageCounterRef.current}`;
    }, []);

    const buildTextMessage = useCallback((payload: AppendTextMessagePayload) => ({
        id: nextMessageId(),
        kind: "text" as const,
        role: payload.role,
        text: payload.text,
    }), [nextMessageId]);

    // Appends a normal text message to the chat history.
    const appendMessage = useCallback((payload: AppendTextMessagePayload) => {
        setMessages((previousMessages) => [...previousMessages, buildTextMessage(payload)]);
    }, [buildTextMessage]);

    // Creates one progress card message and returns its id for future step updates.
    const startProgressMessage = useCallback(
        (scope: "agentic" | "selection", initialStageText: string): string => {
            const id = nextMessageId();
            const initialStep: ChatProgressStep = {
                stage: "started",
                message: initialStageText,
            };
            const message: ChatProgressMessage = {
                id,
                kind: "progress",
                role: "ai",
                status: "running",
                scope,
                currentStageText: initialStageText,
                steps: [initialStep],
            };
            setMessages((previousMessages) => [...previousMessages, message]);
            return id;
        },
        [nextMessageId]
    );

    // Appends one backend progress event into a progress card timeline.
    const pushProgressStep = useCallback((messageId: string, event: ModificationProgressEvent) => {
        const stage = String(event.stage || "").trim() || "processing";
        const detail = String(event.message || "").trim() || "Working...";
        const batchId = typeof event.batchId === "number" ? event.batchId : undefined;
        const incomingStatus = normalizeProgressStatus(event.status);

        setMessages((previousMessages) =>
            previousMessages.map((message) => {
                if (message.id !== messageId || message.kind !== "progress") return message;

                const latestStep = message.steps[message.steps.length - 1];
                const isDuplicateStep =
                    !!latestStep &&
                    latestStep.stage === stage &&
                    latestStep.message === detail &&
                    latestStep.batchId === batchId &&
                    incomingStatus === message.status;

                const nextSteps = isDuplicateStep
                    ? message.steps
                    : [...message.steps, { stage, message: detail, batchId }];

                return {
                    ...message,
                    // Preserve failed status once entered; do not set "completed" from intermediate stage events.
                    status: message.status === "failed" || incomingStatus === "failed" ? "failed" : "running",
                    currentStageText: formatCurrentStageText(stage, detail, batchId),
                    steps: nextSteps,
                };
            })
        );
    }, []);

    // Finalizes one progress card when the related request ends.
    const finishProgressMessage = useCallback(
        (
            messageId: string,
            finalStatus: "completed" | "failed",
            finalStageText?: string
        ) => {
            setMessages((previousMessages) =>
                previousMessages.map((message) => {
                    if (message.id !== messageId || message.kind !== "progress") return message;

                    if (!finalStageText) {
                        return {
                            ...message,
                            status: finalStatus,
                        };
                    }

                    const normalizedFinalText = finalStageText.trim();
                    const latestStep = message.steps[message.steps.length - 1];
                    const nextSteps = latestStep?.message === normalizedFinalText
                        ? message.steps
                        : [...message.steps, { stage: finalStatus, message: normalizedFinalText }];

                    return {
                        ...message,
                        status: finalStatus,
                        currentStageText: normalizedFinalText,
                        steps: nextSteps,
                    };
                })
            );
        },
        []
    );

    // Function to handle sending a query to the backend and updating the chat history with the response.
    const handleQuery = useCallback(async () => {
        const textInput = input.trim();
        if (!textInput || isQuerying) {
            return;
        }

        setIsQuerying(true);
        const userMessage = buildTextMessage({ role: "user", text: textInput });
        const placeholderMessage = buildTextMessage({ role: "ai", text: "Processing..." });
        setMessages((previousMessages) => [...previousMessages, userMessage, placeholderMessage]);
        setInput("");

        try {
            const response = await axios.post(`${API_BASE}/api/query`, {
                query: textInput,
            });

            // Replace the placeholder AI response with the actual response from the backend.
            setMessages((previousMessages) => [
                ...previousMessages.slice(0, -1),
                buildTextMessage({ role: "ai", text: response.data.answer || "(no response)" }),
            ]);
        } catch {
            // If there's an error, replace the placeholder with an error message.
            setMessages((previousMessages) => [
                ...previousMessages.slice(0, -1),
                buildTextMessage({ role: "ai", text: "Error: Failed to get response from server." }),
            ]);
        } finally {
            setIsQuerying(false);
        }
    }, [buildTextMessage, input, isQuerying]);

    // Handler for keydown events in the chat input, to allow sending the query with Enter key.
    const handleKeyDown = useCallback(
        (event: KeyboardEvent<HTMLTextAreaElement>) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void handleQuery();
            }
        },
        [handleQuery]
    );

    return {
        messages,
        input,
        isQuerying,
        setInput,
        appendMessage,
        startProgressMessage,
        pushProgressStep,
        finishProgressMessage,
        handleQuery,
        handleKeyDown,
    };
}
