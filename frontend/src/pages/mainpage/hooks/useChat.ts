import { useCallback, useState, type KeyboardEvent } from "react";
import axios from "axios";
import type { ChatMessage } from "../types";

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
    const [input, setInput] = useState("");
    const [isQuerying, setIsQuerying] = useState(false);
    const [conversationId, setConversationId] = useState<string | null>(null);

    // Function to append a new message to the chat history
    const appendMessage = useCallback((message: ChatMessage) => {
        setMessages((previousMessages) => [...previousMessages, message]);
    }, []);

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

        } catch (error) {
            setMessages((previousMessages) =>
                // If there's an error, replace the placeholder with an error message
                [...previousMessages.slice(0, -1), { role: "ai", text: "Error: Failed to get response from server." }]
            );
        } finally {
            setIsQuerying(false);
        }
    }, [conversationId, input, isQuerying]); // Update the function whenever the input or querying state changes to ensure it has the latest values.

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
        input,
        isQuerying,
        conversationId,
        userEmail: getResolvedUserEmail(),
        setInput,
        setTestUserEmail,
        clearTestUserEmail,
        appendMessage,
        handleQuery,
        handleKeyDown,
    };
}
