import { useCallback, useState, type KeyboardEvent } from "react";
import axios from "axios";
import type { ChatMessage } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

// Custom hook to manage chat state and interactions
export function useChat() {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState("");
    const [isQuerying, setIsQuerying] = useState(false);

    // Function to append a new message to the chat history
    const appendMessage = useCallback((message: ChatMessage) => {
        setMessages((previousMessages) => [...previousMessages, message]);
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
            const response = await axios.post(`${API_BASE}/api/query`, {
                query: textInput,
            });

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
    }, [input, isQuerying]); // Update the function whenever the input or querying state changes to ensure it has the latest values.

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
        setInput,
        appendMessage,
        handleQuery,
        handleKeyDown,
    };
}
