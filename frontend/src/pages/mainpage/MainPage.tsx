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

type DocumentItem = {
    id: string;
    fileName: string;
    content: string;
    size: number;
    chunks: number;
};

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

export default function MainPage() {
    const navigate = useNavigate();

    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState("");

    // Modification Panel State
    const [isModificationPanelOpen, setIsModificationPanelOpen] = useState(false);
    const [documents, setDocuments] = useState<DocumentItem[]>([]);
    const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
    const [checkedDocs, setCheckedDocs] = useState<Set<string>>(new Set());
    const [isLoadingDocs, setIsLoadingDocs] = useState(false);
    const [isDocsCached, setIsDocsCached] = useState(false); // 标记是否已缓存数据

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

    // Fetch documents when modification panel opens (only on first load)
    useEffect(() => {
        if (isModificationPanelOpen && !isDocsCached) {
            fetchDocuments();
        }
    }, [isModificationPanelOpen, isDocsCached]);

    const fetchDocuments = async () => {
        setIsLoadingDocs(true);
        try {
            const response = await axios.get(`${API_BASE}/api/modifications/list`);
            const docs = response.data.documents || response.data;
            setDocuments(docs);
            setIsDocsCached(true); // 标记数据已缓存
            if (docs.length > 0 && !selectedDocId) {
                setSelectedDocId(docs[0].id);
            }
        } catch (error) {
            console.error("Error fetching documents:", error);
        } finally {
            setIsLoadingDocs(false);
        }
    };

    const handleRefreshDocuments = async () => {
        // 手动刷新文档列表
        await fetchDocuments();
    };

    const handleToggleModificationPanel = () => {
        setIsModificationPanelOpen(!isModificationPanelOpen);
    };

    const handleDocumentSelect = (docId: string) => {
        setSelectedDocId(docId);
    };

    const handleDocumentCheck = (docId: string, checked: boolean) => {
        const newChecked = new Set(checkedDocs);
        if (checked) {
            newChecked.add(docId);
        } else {
            newChecked.delete(docId);
        }
        setCheckedDocs(newChecked);
    };

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
        <div className={`app-root ${isModificationPanelOpen ? "with-mod-panel" : ""}`}>
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
                            className={`modification-toggle ${isModificationPanelOpen ? "active" : ""}`}
                            onClick={handleToggleModificationPanel}
                            aria-label="Toggle modifications panel"
                            title="Toggle modifications"
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
                                <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L21 6"></path>
                            </svg>
                        </button>
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

            {/* Modification Panel */}
            {isModificationPanelOpen && (
                <aside className="modification-panel">
                    <div className="mod-panel-header">
                        <h3>🔧 Modifications</h3>
                        <div className="mod-panel-header-actions">
                            <button
                                className="mod-panel-refresh-btn"
                                onClick={handleRefreshDocuments}
                                disabled={isLoadingDocs}
                                aria-label="Refresh documents"
                                title="Refresh from database"
                            >
                                <svg
                                    width="16"
                                    height="16"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                >
                                    <polyline points="23 4 23 10 17 10"></polyline>
                                    <polyline points="1 20 1 14 7 14"></polyline>
                                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36M20.49 15a9 9 0 0 1-14.85 3.36"></path>
                                </svg>
                            </button>
                            <button
                                className="mod-panel-close-btn"
                                onClick={() => setIsModificationPanelOpen(false)}
                                aria-label="Close modifications panel"
                            >
                                ✕
                            </button>
                        </div>
                    </div>

                    {/* Preview Section - Top Half */}
                    <div className="mod-panel-preview-section">
                        <h4>Document Preview</h4>
                        {isLoadingDocs ? (
                            <div className="mod-panel-loading">Loading documents...</div>
                        ) : selectedDocId && documents.find(d => d.id === selectedDocId) ? (
                            <div className="mod-panel-preview-content">
                                <div className="preview-doc-info">
                                    <strong>{documents.find(d => d.id === selectedDocId)?.fileName}</strong>
                                    <span className="preview-meta">
                                        {documents.find(d => d.id === selectedDocId)?.chunks} chunks
                                    </span>
                                </div>
                                <div className="preview-text">
                                    {documents.find(d => d.id === selectedDocId)?.content}
                                </div>
                            </div>
                        ) : (
                            <div className="mod-panel-empty">No document selected</div>
                        )}
                    </div>

                    {/* File List Section - Bottom Half */}
                    <div className="mod-panel-list-section">
                        <h4>Available Documents</h4>
                        <div className="mod-panel-file-list">
                            {isLoadingDocs ? (
                                <div className="mod-panel-loading">Loading...</div>
                            ) : documents.length === 0 ? (
                                <div className="mod-panel-empty">No documents available</div>
                            ) : (
                                documents.map((doc) => (
                                    <div
                                        key={doc.id}
                                        className={`mod-panel-file-item ${selectedDocId === doc.id ? "active" : ""}`}
                                    >
                                        <input
                                            type="checkbox"
                                            checked={checkedDocs.has(doc.id)}
                                            onChange={(e) => handleDocumentCheck(doc.id, e.target.checked)}
                                            className="mod-panel-checkbox"
                                        />
                                        <span
                                            className="mod-panel-file-name"
                                            onClick={() => handleDocumentSelect(doc.id)}
                                        >
                                            {doc.fileName}
                                        </span>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                </aside>
            )}
        </div>
    );
}
