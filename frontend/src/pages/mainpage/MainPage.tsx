import React, { useState, useRef, useEffect } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "./MainPage.css";
import "highlight.js/styles/github.css";


type ChatMessage = {
    role: "user" | "ai";
    text: string;
};

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

export default function MainPage() {
    const navigate = useNavigate();

    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState("");

    // File State
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [fileContent, setFileContent] = useState<string>("");

    // Loading States
    const [isQuerying, setIsQuerying] = useState<boolean>(false);
    const [isUploading, setIsUploading] = useState<boolean>(false);

    // Refs
    const fileRef = useRef<HTMLInputElement | null>(null);
    const bottomRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isQuerying, isUploading]);

    const handleLogout = () => {
        localStorage.removeItem("token");
        navigate("/register");
    };

    // --- File Handlers ---
    const handleFileSelectClick = () => {
        fileRef.current?.click();
    };

    const onFileChange: React.ChangeEventHandler<HTMLInputElement> = (event) => {
        const file = event.target.files?.[0] || null;
        setSelectedFile(file);

        if (file) {
            const reader = new FileReader();
            reader.onload = () => {
                const base64String = (reader.result as string).split(",")[1];
                setFileContent(base64String);
            };
            reader.readAsDataURL(file);
        } else {
            setFileContent("");
        }
    };

    const clearFile = () => {
        setSelectedFile(null);
        setFileContent("");
        if (fileRef.current) fileRef.current.value = "";
    };

    const handleUpload = async () => {
        if (!fileContent || !selectedFile || isUploading) return;
        setIsUploading(true);
        try {
            await axios.post(`${API_BASE}/ingest/webhook`, {
                fileName: selectedFile.name,
                contentType: selectedFile.type || "application/octet-stream",
                data: fileContent,
            });

            setMessages((prev) => [
                ...prev,
                { role: "ai", text: `"${selectedFile.name}" has been added to the knowledge base.` },
            ]);
            clearFile();
        } catch (error) {
            console.error("Error ingesting file:", error);
            setMessages((prev) => [
                ...prev,
                { role: "ai", text: `Failed to upload "${selectedFile?.name ?? "file"}".` },
            ]);
        } finally {
            setIsUploading(false);
        }
    };

    // --- Chat Handlers ---
    const handleQuery = async () => {
        const textInput = input.trim();
        if (!textInput || isQuerying) return;

        setIsQuerying(true);
        const newMessage: ChatMessage = { role: "user", text: textInput };
        let placeholderIndex = -1;

        setMessages((prev) => {
            placeholderIndex = prev.length + 1; // index of the placeholder
            return [...prev, newMessage, { role: "ai" as const, text: "Processing…" }];
        });
        setInput("");

        try {
            const response = await axios.post(`${API_BASE}/api/query`, {
                query: textInput,
            });

            setMessages((prev) =>
                prev.map((msg, idx) =>
                    idx === placeholderIndex
                        ? { role: "ai", text: response.data.answer || "(no response)" }
                        : msg
                )
            );
        } catch {
            setMessages((prev) =>
                prev.map((msg, idx) =>
                    idx === placeholderIndex
                        ? { role: "ai", text: "Error: Unable to reach backend" }
                        : msg
                )
            );
        } finally {
            setIsQuerying(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleQuery();
        }
    };

    return (
        <div className="app-root">
            <aside className="sidebar">
                <div className="sidebar-header">
                    <div className="logo-mark">KB</div>
                    <div>
                        <div className="eyebrow">Workspace</div>
                        <div className="sidebar-title">Upload sources</div>
                    </div>
                </div>
                <p className="sidebar-hint">PDF, DOCX or TXT - keep everything you need for the chat here.</p>

                <div className="sources-section">
                    <div className="section-title">Files</div>

                    <input
                        ref={fileRef}
                        type="file"
                        className="hidden-file-input"
                        style={{ display: "none" }}
                        onChange={onFileChange}
                        accept=".pdf,.doc,.docx,.txt"
                    />

                    {!selectedFile && (
                        <button className="add-source-btn" onClick={handleFileSelectClick}>
                            <span className="plus-icon" aria-hidden>
                                +
                            </span>
                            Select file
                        </button>
                    )}

                    {selectedFile && (
                        <div className="source-card active">
                            <div className="file-info">
                                <span className="file-icon" aria-hidden>
                                    DOC
                                </span>
                                <span className="file-name">{selectedFile.name}</span>
                            </div>
                            <div className="file-actions">
                                <button
                                    className="action-btn upload-confirm-btn"
                                    onClick={handleUpload}
                                    disabled={isUploading}
                                >
                                    {isUploading ? "Uploading..." : "Confirm upload"}
                                </button>
                                <button className="action-btn remove-btn" onClick={clearFile} disabled={isUploading}>
                                    Remove
                                </button>
                            </div>
                        </div>
                    )}

                </div>
            </aside>

            <main className="main-content">
                <header className="top-nav">
                    <div>
                        <div className="nav-eyebrow">Document chat</div>
                        <div className="nav-title">Ask your documents</div>
                    </div>
                    <div className="nav-actions">
                        <button className="nav-btn" onClick={handleLogout}>
                            Logout
                        </button>
                    </div>
                </header>

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
                                    <div className="message-avatar">{msg.role === "user" ? "You" : "AI"}</div>
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
                                        <span className="typing-indicator">
                                            Reading document...
                                        </span>
                                    </div>
                                </div>
                            )}
                            <div ref={bottomRef} />
                        </div>
                    )}
                </div>

                <div className="input-area-wrapper">
                    <div className="input-container">
                        <textarea
                            className="chat-input"
                            placeholder="Ask something about your files..."
                            rows={1}
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={handleKeyDown}
                        />
                        <button
                            className="send-icon-btn"
                            onClick={handleQuery}
                            disabled={!input.trim() || isQuerying}
                            aria-label="Send message"
                        >
                            <svg
                                width="20"
                                height="20"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                            >
                                <line x1="22" y1="2" x2="11" y2="13"></line>
                                <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                            </svg>
                        </button>
                    </div>
                    <div className="input-hint">Enter to send | Shift+Enter for a new line</div>
                </div>
            </main>
        </div>
    );
}
