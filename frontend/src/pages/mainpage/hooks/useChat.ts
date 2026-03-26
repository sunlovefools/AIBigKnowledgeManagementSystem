import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { apiClient } from "../../../auth/apiClient";
import type {
    ChatMessage,
    ChatProgressMessage,
    ChatProgressStep,
    ChatRole,
    ChatTextMessage,
    ConversationSummary,
} from "../types";
import type { ModificationProgressEvent } from "./documents/api/documentsApi";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
const CHAT_TEST_USER_EMAIL_STORAGE_KEY = "chatTestUserEmail";

type AppendTextMessagePayload = {
    role: ChatRole;
    text: string;
};

type ConversationApiMessage = {
    messageId?: string;
    role?: string;
    text?: string;
    timestamp?: string;
    userEmail?: string;
};

function getResolvedUserEmail() {
    const testUserEmail = localStorage.getItem(CHAT_TEST_USER_EMAIL_STORAGE_KEY)?.trim();
    if (testUserEmail) return testUserEmail;

    const authUserEmail = localStorage.getItem("userEmail")?.trim();
    if (authUserEmail) return authUserEmail;

    const envUserEmail = import.meta.env.VITE_CHAT_TEST_USER_EMAIL?.trim();
    if (envUserEmail) return envUserEmail;

    return undefined;
}

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

function normalizeApiRole(raw: string | undefined): ChatRole {
    return raw === "user" ? "user" : "ai";
}

export function useChat() {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);
    const [input, setInput] = useState("");
    const [isQuerying, setIsQuerying] = useState(false);
    const [isLoadingConversations, setIsLoadingConversations] = useState(false);
    const [isLoadingConversationMessages, setIsLoadingConversationMessages] = useState(false);
    const [conversationsError, setConversationsError] = useState<string | null>(null);
    const [conversationMessagesError, setConversationMessagesError] = useState<string | null>(null);
    const [conversationId, setConversationId] = useState<string | null>(null);
    const [conversationMessagesCursor, setConversationMessagesCursor] = useState<number | null>(null);
    const [hasMoreConversationMessages, setHasMoreConversationMessages] = useState(false);
    const [isLoadingMoreConversationMessages, setIsLoadingMoreConversationMessages] = useState(false);
    const messageCounterRef = useRef(0);

    const nextMessageId = useCallback(() => {
        messageCounterRef.current += 1;
        return `msg-${Date.now()}-${messageCounterRef.current}`;
    }, []);

    const buildTextMessage = useCallback((payload: AppendTextMessagePayload): ChatTextMessage => ({
        id: nextMessageId(),
        kind: "text",
        role: payload.role,
        text: payload.text,
    }), [nextMessageId]);

    const toConversationTextMessage = useCallback(
        (message: ConversationApiMessage, fallbackIndex: number): ChatTextMessage => ({
            id: message.messageId?.trim() || `hist-${Date.now()}-${fallbackIndex}-${nextMessageId()}`,
            kind: "text",
            role: normalizeApiRole(message.role),
            text: String(message.text || ""),
            messageId: message.messageId,
            timestamp: message.timestamp,
            userEmail: message.userEmail,
        }),
        [nextMessageId]
    );

    const appendMessage = useCallback((payload: AppendTextMessagePayload) => {
        setMessages((previousMessages) => [...previousMessages, buildTextMessage(payload)]);
    }, [buildTextMessage]);

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

    const setTestUserEmail = useCallback((email: string) => {
        const normalizedEmail = email.trim();
        if (!normalizedEmail) {
            localStorage.removeItem(CHAT_TEST_USER_EMAIL_STORAGE_KEY);
            return;
        }
        localStorage.setItem(CHAT_TEST_USER_EMAIL_STORAGE_KEY, normalizedEmail);
    }, []);

    const clearTestUserEmail = useCallback(() => {
        localStorage.removeItem(CHAT_TEST_USER_EMAIL_STORAGE_KEY);
    }, []);

    const refreshConversations = useCallback(async () => {
        const userEmail = getResolvedUserEmail();
        if (!userEmail) {
            setConversations([]);
            setConversationsError("Set a test user email to load conversations.");
            return;
        }

        setIsLoadingConversations(true);
        setConversationsError(null);

        try {
            const response = await apiClient.get(`${API_BASE}/api/conversations`, {
                params: {
                    user_email: userEmail,
                    limit: 100,
                },
            });

            const nextConversations = Array.isArray(response.data?.conversations)
                ? (response.data.conversations as ConversationSummary[])
                : [];
            setConversations(nextConversations);
        } catch {
            setConversationsError("Failed to load conversations.");
        } finally {
            setIsLoadingConversations(false);
        }
    }, []);

    const loadConversationMessages = useCallback(async (targetConversationId: string) => {
        const userEmail = getResolvedUserEmail();
        if (!userEmail) {
            setConversationMessagesError("Set a test user email to load conversation messages.");
            return;
        }

        setIsLoadingConversationMessages(true);
        setConversationMessagesError(null);

        try {
            const response = await apiClient.get(`${API_BASE}/api/conversations/${targetConversationId}/messages`, {
                params: {
                    user_email: userEmail,
                    limit: 50,
                    cursor: 0,
                },
            });

            const nextMessages = Array.isArray(response.data?.messages)
                ? (response.data.messages as ConversationApiMessage[]).map(toConversationTextMessage)
                : [];

            setMessages(nextMessages);
            setConversationId(targetConversationId);
            const nextCursor = Number(response.data?.nextCursor);
            setConversationMessagesCursor(Number.isFinite(nextCursor) ? nextCursor : null);
            setHasMoreConversationMessages(Boolean(response.data?.hasMore));
        } catch {
            setConversationMessagesError("Failed to load selected conversation.");
        } finally {
            setIsLoadingConversationMessages(false);
        }
    }, [toConversationTextMessage]);

    const loadMoreConversationMessages = useCallback(async () => {
        const userEmail = getResolvedUserEmail();
        if (!userEmail || !conversationId || conversationMessagesCursor === null || isLoadingMoreConversationMessages) {
            return;
        }

        setIsLoadingMoreConversationMessages(true);
        setConversationMessagesError(null);

        try {
            const response = await apiClient.get(`${API_BASE}/api/conversations/${conversationId}/messages`, {
                params: {
                    user_email: userEmail,
                    limit: 50,
                    cursor: conversationMessagesCursor,
                },
            });

            const olderMessages = Array.isArray(response.data?.messages)
                ? (response.data.messages as ConversationApiMessage[]).map(toConversationTextMessage)
                : [];

            setMessages((previousMessages) => [...olderMessages, ...previousMessages]);
            const nextCursor = Number(response.data?.nextCursor);
            setConversationMessagesCursor(Number.isFinite(nextCursor) ? nextCursor : null);
            setHasMoreConversationMessages(Boolean(response.data?.hasMore));
        } catch {
            setConversationMessagesError("Failed to load more conversation messages.");
        } finally {
            setIsLoadingMoreConversationMessages(false);
        }
    }, [conversationId, conversationMessagesCursor, isLoadingMoreConversationMessages, toConversationTextMessage]);

    const startNewConversation = useCallback(() => {
        setConversationId(null);
        setMessages([]);
        setConversationMessagesError(null);
        setConversationMessagesCursor(null);
        setHasMoreConversationMessages(false);
        setIsLoadingMoreConversationMessages(false);
    }, []);

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
                    status: message.status === "failed" || incomingStatus === "failed" ? "failed" : "running",
                    currentStageText: formatCurrentStageText(stage, detail, batchId),
                    steps: nextSteps,
                };
            })
        );
    }, []);

    const renameConversation = useCallback(async (targetConversationId: string, newTitle: string) => {
        const userEmail = getResolvedUserEmail();
        if (!userEmail) {
            setConversationsError("Set a test user email to rename conversations.");
            return false;
        }

        if (!newTitle.trim()) {
            setConversationsError("Title cannot be empty.");
            return false;
        }

        try {
            await apiClient.patch(
                `${API_BASE}/api/conversations/${targetConversationId}/title`,
                { title: newTitle.trim() },
                { params: { user_email: userEmail } }
            );
            void refreshConversations();
            return true;
        } catch {
            setConversationsError("Failed to rename conversation.");
            return false;
        }
    }, [refreshConversations]);

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
            const userEmail = getResolvedUserEmail();
            const response = await apiClient.post(`${API_BASE}/api/query`, {
                query: textInput,
                conversation_id: conversationId,
                user_email: userEmail,
            });

            if (typeof response.data?.conversation_id === "string") {
                setConversationId(response.data.conversation_id);
            }

            setMessages((previousMessages) => [
                ...previousMessages.slice(0, -1),
                buildTextMessage({ role: "ai", text: response.data.answer || "(no response)" }),
            ]);
            void refreshConversations();
        } catch {
            setMessages((previousMessages) => [
                ...previousMessages.slice(0, -1),
                buildTextMessage({ role: "ai", text: "Error: Failed to get response from server." }),
            ]);
        } finally {
            setIsQuerying(false);
        }
    }, [buildTextMessage, conversationId, input, isQuerying, refreshConversations]);

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
        conversations,
        input,
        isQuerying,
        isLoadingConversations,
        isLoadingConversationMessages,
        conversationsError,
        conversationMessagesError,
        conversationId,
        hasMoreConversationMessages,
        isLoadingMoreConversationMessages,
        userEmail: getResolvedUserEmail(),
        setInput,
        setTestUserEmail,
        clearTestUserEmail,
        appendMessage,
        startProgressMessage,
        pushProgressStep,
        finishProgressMessage,
        refreshConversations,
        loadConversationMessages,
        loadMoreConversationMessages,
        renameConversation,
        startNewConversation,
        handleQuery,
        handleKeyDown,
    };
}
