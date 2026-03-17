import { useCallback, useState, type KeyboardEvent } from "react";
import axios from "axios";
import type { ChatMessage, ConversationSummary } from "../types";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
const CHAT_TEST_USER_EMAIL_STORAGE_KEY = "chatTestUserEmail";

function getResolvedUserEmail() {
    const testUserEmail = localStorage.getItem(CHAT_TEST_USER_EMAIL_STORAGE_KEY)?.trim();
    if (testUserEmail) {
        return testUserEmail;
    }

    const authUserEmail = localStorage.getItem("userEmail")?.trim();
    if (authUserEmail) {
        return authUserEmail;
    }

    const envUserEmail = import.meta.env.VITE_CHAT_TEST_USER_EMAIL?.trim();
    if (envUserEmail) {
        return envUserEmail;
    }

    return undefined;
}

// Custom hook to manage chat state and interactions
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

    // Function to append a new message to the chat history
    const appendMessage = useCallback((message: ChatMessage) => {
        setMessages((previousMessages) => [...previousMessages, message]);// Append the new message to the existing list of messages in the state
    }, []);

    const setTestUserEmail = useCallback((email: string) => {// Function to set a test user email in local storage, which can be used to simulate different users in the chat
        const normalizedEmail = email.trim();
        if (!normalizedEmail) {
            localStorage.removeItem(CHAT_TEST_USER_EMAIL_STORAGE_KEY);
            return;
        }

        localStorage.setItem(CHAT_TEST_USER_EMAIL_STORAGE_KEY, normalizedEmail);// Store the normalized email in local storage under a specific key
    }, []);

    const clearTestUserEmail = useCallback(() => {// Function to clear the test user email from local storage, effectively resetting to the default user email resolution behavior
        localStorage.removeItem(CHAT_TEST_USER_EMAIL_STORAGE_KEY);
    }, []);

    const refreshConversations = useCallback(async () => {// Function to load the list of conversations for the current user from the backend API, and update the state with the retrieved conversations
        const userEmail = getResolvedUserEmail();
        if (!userEmail) {
            setConversations([]);
            setConversationsError("Set a test user email to load conversations.");
            return;
        }

        setIsLoadingConversations(true);
        setConversationsError(null);

        try {// Make a GET request to the backend API to retrieve the list of conversations for the user, passing the user email as a query parameter
            const response = await axios.get(`${API_BASE}/api/conversations`, {
                params: {
                    user_email: userEmail,
                    limit: 100,
                },
            });

            const nextConversations = Array.isArray(response.data?.conversations)// Check if the response contains a conversations array, and if so, cast it to the expected type; otherwise, use an empty array
                ? (response.data.conversations as ConversationSummary[])
                : [];
            setConversations(nextConversations);
        } catch (error) {
            setConversationsError("Failed to load conversations.");
        } finally {
            setIsLoadingConversations(false);
        }
    }, []);

    // Function to load messages for a specific conversation from the backend API, and update the state with the retrieved messages and conversation ID i.e. when conversation is selected from the list
    const loadConversationMessages = useCallback(async (targetConversationId: string) => {
        const userEmail = getResolvedUserEmail();
        if (!userEmail) {
            setConversationMessagesError("Set a test user email to load conversation messages.");
            return;
        }

        setIsLoadingConversationMessages(true);
        setConversationMessagesError(null);

        try {
            const response = await axios.get(`${API_BASE}/api/conversations/${targetConversationId}/messages`, {
                params: {
                    user_email: userEmail,
                    limit: 50,
                    cursor: 0,
                },
            });

            const nextMessages = Array.isArray(response.data?.messages)// Check if the response contains a messages array, and if so, map it to the expected ChatMessage type; otherwise, use an empty array
                ? (response.data.messages as Array<
                      ChatMessage & {
                          messageId?: string;
                      }
                  >).map((message) => ({
                      messageId: message.messageId,
                      role: message.role,
                      text: message.text,
                      timestamp: message.timestamp,
                      userEmail: message.userEmail,
                  }))
                : [];

            setMessages(nextMessages);
            setConversationId(targetConversationId);
            const nextCursor = Number(response.data?.nextCursor);
            setConversationMessagesCursor(Number.isFinite(nextCursor) ? nextCursor : null);
            setHasMoreConversationMessages(Boolean(response.data?.hasMore));
        } catch (error) {
            setConversationMessagesError("Failed to load selected conversation.");
        } finally {
            setIsLoadingConversationMessages(false);
        }
    }, []);

    const loadMoreConversationMessages = useCallback(async () => {//pagination function to load more messages for the current conversation when user clicks "Load older messages" button, using the conversationMessagesCursor to keep track of pagination state and appending the newly loaded messages to the existing list in the state
        const userEmail = getResolvedUserEmail();
        if (!userEmail || !conversationId || conversationMessagesCursor === null || isLoadingMoreConversationMessages) {
            return;
        }

        setIsLoadingMoreConversationMessages(true);
        setConversationMessagesError(null);

        try {
            const response = await axios.get(`${API_BASE}/api/conversations/${conversationId}/messages`, {
                params: {
                    user_email: userEmail,
                    limit: 50,
                    cursor: conversationMessagesCursor,
                },
            });

            const olderMessages = Array.isArray(response.data?.messages)
                ? (response.data.messages as Array<
                      ChatMessage & {
                          messageId?: string;
                      }
                  >).map((message) => ({
                      messageId: message.messageId,
                      role: message.role,
                      text: message.text,
                      timestamp: message.timestamp,
                      userEmail: message.userEmail,
                  }))
                : [];

            setMessages((previousMessages) => [...olderMessages, ...previousMessages]);
            const nextCursor = Number(response.data?.nextCursor);
            setConversationMessagesCursor(Number.isFinite(nextCursor) ? nextCursor : null);
            setHasMoreConversationMessages(Boolean(response.data?.hasMore));
        } catch (error) {
            setConversationMessagesError("Failed to load more conversation messages.");
        } finally {
            setIsLoadingMoreConversationMessages(false);
        }
    }, [conversationId, conversationMessagesCursor, isLoadingMoreConversationMessages]);

    const startNewConversation = useCallback(() => {// Function to start a new conversation, which clears the current messages and conversation ID from the state, effectively resetting the chat interface for a new conversation
        setConversationId(null);
        setMessages([]);
        setConversationMessagesError(null);
        setConversationMessagesCursor(null);
        setHasMoreConversationMessages(false);
        setIsLoadingMoreConversationMessages(false);
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
            await axios.patch(`${API_BASE}/api/conversations/${targetConversationId}/title`, 
                { title: newTitle.trim() },
                { params: { user_email: userEmail } }
            );
            
            // Refresh conversations to reflect the renamed title
            void refreshConversations();
            return true;
        } catch (error) {
            setConversationsError("Failed to rename conversation.");
            return false;
        }
    }, [refreshConversations]);

    // Function to handle sending a query to the backend and updating the chat history with the response
    const handleQuery = useCallback(async () => {
        const textInput = input.trim();
        if (!textInput || isQuerying) {
            return;
        }

        setIsQuerying(true); // Set the querying state to true to prevent multiple simultaneous queries
        const newMessage: ChatMessage = { role: "user", text: textInput }; // Create a new message object for the user's input

        // Append the user's message and a placeholder AI response to the chat history
        setMessages((previousMessages) => 
            [...previousMessages, newMessage, { role: "ai", text: "Processing..." }]);

        setInput("");

        try {
            const userEmail = getResolvedUserEmail();
            const response = await axios.post(`${API_BASE}/api/query`, {
                query: textInput,
                conversation_id: conversationId,
                user_email: userEmail,
            });

            if (typeof response.data?.conversation_id === "string") {
                setConversationId(response.data.conversation_id);
            }

            // Replace the placeholder AI response with the actual response from the backend
            setMessages((previousMessages) =>
                [...previousMessages.slice(0, -1), { role: "ai", text: response.data.answer || "(no response)" }]);

            void refreshConversations();// Refresh the conversation list to reflect any updates (like new conversations or updated timestamps)

        } catch (error) {
            setMessages((previousMessages) =>
                // If there's an error, replace the placeholder with an error message
                [...previousMessages.slice(0, -1), { role: "ai", text: "Error: Failed to get response from server." }]
            );
        } finally {
            setIsQuerying(false);
        }
    }, [conversationId, input, isQuerying, refreshConversations]); // Update the function whenever the input or querying state changes to ensure it has the latest values.

    // Handler for keydown events in the chat input, to allow sending the query with Enter key
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
        refreshConversations,
        loadConversationMessages,
        loadMoreConversationMessages,
        renameConversation,
        startNewConversation,
        handleQuery,
        handleKeyDown,
    };
}
