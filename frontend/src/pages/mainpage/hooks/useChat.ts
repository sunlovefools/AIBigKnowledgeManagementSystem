import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { apiClient, authenticatedFetch } from "../../../auth/apiClient";
import type {
    ChatMessage,
    ChatProgressMessage,
    ChatProgressStep,
    ChatRole,
    ChatTextMessage,
    ConversationSummary,
} from "../types";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
const CHAT_TEST_USER_EMAIL_STORAGE_KEY = "chatTestUserEmail";
const AGENTIC_SEARCH_ENABLED_STORAGE_KEY = "agenticSearchEnabled";

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

type HandleQueryOptions = {
    collectionId?: string | null;
};

type AgenticQueryResponsePayload = {
    answer?: string;
    citations?: unknown[];
    conversation_id?: string;
};

type AgenticQueryProgressEvent = {
    stage?: string;
    status?: string;
    message?: string;
    timestamp?: string;
    batchId?: number;
    metadata?: Record<string, unknown>;
};

type ProgressEventWithMetadata = {
    stage?: string;
    status?: string;
    message?: string;
    batchId?: number;
    metadata?: Record<string, unknown>;
};

type StreamEvent = {
    event: string;
    data: string;
};

function getApiErrorDetail(error: unknown): string | null {
    if (!error || typeof error !== "object") return null;
    const maybeResponse = (error as { response?: { data?: { detail?: unknown } } }).response;
    const detail = maybeResponse?.data?.detail;
    return typeof detail === "string" && detail.trim() ? detail.trim() : null;
}

function parseStreamEvent(rawChunk: string): StreamEvent | null {
    const lines = rawChunk
        .split("\n")
        .map((line) => line.trimEnd())
        .filter((line) => line.length > 0 && !line.startsWith(":"));
    if (!lines.length) return null;

    let event = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
        if (line.startsWith("event:")) {
            event = line.slice("event:".length).trim();
            continue;
        }
        if (line.startsWith("data:")) {
            dataLines.push(line.slice("data:".length).trimStart());
        }
    }
    if (!dataLines.length) return null;
    return { event, data: dataLines.join("\n") };
}

async function readAgenticQueryStreamResult(
    response: Response,
    onProgress?: (progress: AgenticQueryProgressEvent) => void
): Promise<AgenticQueryResponsePayload> {
    if (!response.ok) {
        const bodyText = await response.text();
        let detail = bodyText || `Request failed with status ${response.status}.`;
        try {
            const parsed = JSON.parse(bodyText) as { detail?: unknown };
            if (typeof parsed.detail === "string" && parsed.detail.trim()) {
                detail = parsed.detail;
            }
        } catch {
            // Keep text fallback.
        }
        throw new Error(detail);
    }

    if (!response.body) {
        throw new Error("No response stream received from server.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let result: AgenticQueryResponsePayload | null = null;

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        buffer = buffer.replace(/\r/g, "");
        let boundaryIndex = buffer.indexOf("\n\n");
        while (boundaryIndex !== -1) {
            const rawEvent = buffer.slice(0, boundaryIndex);
            buffer = buffer.slice(boundaryIndex + 2);

            const parsed = parseStreamEvent(rawEvent);
            if (parsed) {
                if (parsed.event === "progress") {
                    try {
                        onProgress?.(JSON.parse(parsed.data) as AgenticQueryProgressEvent);
                    } catch {
                        // Ignore malformed progress payloads.
                    }
                } else if (parsed.event === "result") {
                    result = JSON.parse(parsed.data) as AgenticQueryResponsePayload;
                } else if (parsed.event === "error") {
                    let detail = "Streaming request failed.";
                    try {
                        const errorPayload = JSON.parse(parsed.data) as { detail?: unknown };
                        if (typeof errorPayload.detail === "string" && errorPayload.detail.trim()) {
                            detail = errorPayload.detail;
                        }
                    } catch {
                        detail = parsed.data || detail;
                    }
                    throw new Error(detail);
                }
            }

            boundaryIndex = buffer.indexOf("\n\n");
        }
    }

    if (result === null) {
        throw new Error("Stream ended without a result payload.");
    }
    return result;
}

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

function optionalText(value: unknown): string | undefined {
    if (typeof value !== "string") return undefined;
    const normalized = value.trim();
    return normalized.length > 0 ? normalized : undefined;
}

function optionalNumber(value: unknown): number | undefined {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
        const parsed = Number(value);
        if (Number.isFinite(parsed)) return parsed;
    }
    return undefined;
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
    const [isAgenticSearchEnabled, setIsAgenticSearchEnabled] = useState<boolean>(() => {
        const raw = localStorage.getItem(AGENTIC_SEARCH_ENABLED_STORAGE_KEY);
        return raw === "1";
    });
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
        (scope: "agentic" | "selection" | "agentic-search", initialStageText: string): string => {
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

    const toggleAgenticSearch = useCallback(() => {
        setIsAgenticSearchEnabled((previous) => {
            const next = !previous;
            localStorage.setItem(AGENTIC_SEARCH_ENABLED_STORAGE_KEY, next ? "1" : "0");
            return next;
        });
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
                },
            });

            const nextMessages = Array.isArray(response.data?.messages)
                ? (response.data.messages as ConversationApiMessage[]).map(toConversationTextMessage)
                : [];

            setMessages(nextMessages);
            setConversationId(targetConversationId);
        } catch {
            setConversationMessagesError("Failed to load selected conversation.");
        } finally {
            setIsLoadingConversationMessages(false);
        }
    }, [toConversationTextMessage]);

    const startNewConversation = useCallback(() => {
        setConversationId(null);
        setMessages([]);
        setConversationMessagesError(null);
    }, []);

    const pushProgressStep = useCallback((messageId: string, event: ProgressEventWithMetadata) => {
        const stage = String(event.stage || "").trim() || "processing";
        const detail = String(event.message || "").trim() || "Working...";
        const batchId = typeof event.batchId === "number" ? event.batchId : undefined;
        const incomingStatus = normalizeProgressStatus(event.status);
        const metadata = event.metadata && typeof event.metadata === "object"
            ? event.metadata
            : undefined;
        const step = optionalNumber(metadata?.step);
        const tool = optionalText(metadata?.tool) || optionalText(metadata?.action);
        const intent = optionalText(metadata?.intent);
        const decision = optionalText(metadata?.decision);
        const observation = optionalText(metadata?.observation);
        const argumentsPreview = optionalText(metadata?.argumentsPreview);
        const stagePrefixParts: string[] = [];
        if (typeof step === "number") {
            stagePrefixParts.push(`Step ${step}`);
        }
        if (tool) {
            stagePrefixParts.push(tool);
        }
        const stagePrefix = stagePrefixParts.length > 0 ? `${stagePrefixParts.join(" | ")}: ` : "";
        const currentStageText = stagePrefix
            ? stagePrefix + detail
            : formatCurrentStageText(stage, detail, batchId);
        const incomingStep: ChatProgressStep = {
            stage,
            message: detail,
            batchId,
            ...(typeof step === "number" ? { step } : {}),
            ...(tool ? { tool } : {}),
            ...(intent ? { intent } : {}),
            ...(decision ? { decision } : {}),
            ...(observation ? { observation } : {}),
            ...(argumentsPreview ? { argumentsPreview } : {}),
        };

        setMessages((previousMessages) =>
            previousMessages.map((message) => {
                if (message.id !== messageId || message.kind !== "progress") return message;

                const latestStep = message.steps[message.steps.length - 1];
                const isDuplicateStep =
                    !!latestStep &&
                    latestStep.stage === incomingStep.stage &&
                    latestStep.message === incomingStep.message &&
                    latestStep.batchId === batchId &&
                    latestStep.step === incomingStep.step &&
                    latestStep.tool === incomingStep.tool &&
                    latestStep.intent === incomingStep.intent &&
                    latestStep.decision === incomingStep.decision &&
                    latestStep.observation === incomingStep.observation &&
                    latestStep.argumentsPreview === incomingStep.argumentsPreview &&
                    incomingStatus === message.status;

                const nextSteps = isDuplicateStep
                    ? message.steps
                    : [...message.steps, incomingStep];

                return {
                    ...message,
                    status: message.status === "failed" || incomingStatus === "failed" ? "failed" : "running",
                    currentStageText,
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

    const handleQuery = useCallback(async (options?: HandleQueryOptions) => {
        const textInput = input.trim();
        if (!textInput || isQuerying) {
            return;
        }

        setIsQuerying(true);
        let agenticProgressMessageId: string | null = null;
        const userMessage = buildTextMessage({ role: "user", text: textInput });
        if (isAgenticSearchEnabled) {
            setMessages((previousMessages) => [...previousMessages, userMessage]);
        } else {
            const placeholderMessage = buildTextMessage({ role: "ai", text: "Processing..." });
            setMessages((previousMessages) => [...previousMessages, userMessage, placeholderMessage]);
        }
        setInput("");

        try {
            if (isAgenticSearchEnabled) {
                agenticProgressMessageId = startProgressMessage(
                    "agentic-search",
                    "Agentic search started."
                );
                const response = await authenticatedFetch(`${API_BASE}/api/agent/query-stream`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    body: JSON.stringify({
                        query: textInput,
                        conversation_id: conversationId,
                        collectionId: options?.collectionId ?? null,
                        seed_top_k: 8,
                        max_steps: 6,
                    }),
                });
                const streamResult = await readAgenticQueryStreamResult(
                    response,
                    (progress) => {
                        if (!agenticProgressMessageId) return;
                        pushProgressStep(agenticProgressMessageId, progress);
                    }
                );

                if (typeof streamResult.conversation_id === "string") {
                    setConversationId(streamResult.conversation_id);
                }

                const answerText = String(streamResult.answer || "(no response)");
                const citations = Array.isArray(streamResult.citations)
                    ? streamResult.citations
                        .map((entry: unknown) => String(entry || "").trim())
                        .filter((entry: string) => entry.length > 0)
                    : [];
                const answerWithCitations = citations.length > 0
                    ? `${answerText}\n\n(Sources: ${citations.join(", ")})`
                    : answerText;

                if (agenticProgressMessageId) {
                    finishProgressMessage(agenticProgressMessageId, "completed", "Agentic search completed.");
                }
                appendMessage({ role: "ai", text: answerWithCitations });
                void refreshConversations();
                return;
            }

            const response = await apiClient.post(`${API_BASE}/api/query`, {
                query: textInput,
                conversation_id: conversationId,
                collectionId: options?.collectionId ?? null,
            });

            if (typeof response.data?.conversation_id === "string") {
                setConversationId(response.data.conversation_id);
            }

            setMessages((previousMessages) => [
                ...previousMessages.slice(0, -1),
                buildTextMessage({ role: "ai", text: response.data.answer || "(no response)" }),
            ]);
            void refreshConversations();
        } catch (error) {
            const fallbackError =
                getApiErrorDetail(error)
                || (error instanceof Error ? error.message : null)
                || "Error: Failed to get response from server.";

            if (isAgenticSearchEnabled) {
                if (agenticProgressMessageId) {
                    finishProgressMessage(agenticProgressMessageId, "failed", fallbackError);
                }
                appendMessage({
                    role: "ai",
                    text: fallbackError,
                });
            } else {
                setMessages((previousMessages) => [
                    ...previousMessages.slice(0, -1),
                    buildTextMessage({
                        role: "ai",
                        text: fallbackError,
                    }),
                ]);
            }
        } finally {
            setIsQuerying(false);
        }
    }, [
        appendMessage,
        buildTextMessage,
        conversationId,
        finishProgressMessage,
        input,
        isAgenticSearchEnabled,
        isQuerying,
        pushProgressStep,
        refreshConversations,
        startProgressMessage,
    ]);

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
        isAgenticSearchEnabled,
        userEmail: getResolvedUserEmail(),
        setInput,
        toggleAgenticSearch,
        setTestUserEmail,
        clearTestUserEmail,
        appendMessage,
        startProgressMessage,
        pushProgressStep,
        finishProgressMessage,
        refreshConversations,
        loadConversationMessages,
        renameConversation,
        startNewConversation,
        handleQuery,
        handleKeyDown,
    };
}
