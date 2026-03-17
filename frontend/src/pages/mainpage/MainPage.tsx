import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";

// Helper function to format timestamps as relative time (e.g., "2h ago", "Today", "Yesterday")
function formatRelativeTime(isoTimestamp: string): string {
    try {
        const date = new Date(isoTimestamp);
        const now = new Date();
        const diffMs = now.getTime() - date.getTime();
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffMins < 1) return "just now";
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays === 1) return "yesterday";
        if (diffDays < 7) return `${diffDays}d ago`;

        // For older dates, show date in short format
        return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    } catch {
        return "";
    }
}
import "./MainPage.css";
import "highlight.js/styles/github.css";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import ChatInput from "./components/ChatInput";
import ModificationPanel from "./components/ModificationPanel";
import { useChat } from "./hooks/useChat";
import { useDocuments } from "./hooks/useDocuments";
import { useFileUpload } from "./hooks/useFileUpload";
import { useResizableLayout } from "./hooks/useResizableLayout";

// MainPage component that render the main interface of the application
export default function MainPage() {
    const navigate = useNavigate();
    const [isModificationPanelOpen, setIsModificationPanelOpen] = useState(false);
    const [renamingConversationId, setRenamingConversationId] = useState<string | null>(null);
    const [renameTitle, setRenameTitle] = useState("");
    const [isRenamingConversation, setIsRenamingConversation] = useState(false);
    const bottomRef = useRef<HTMLDivElement | null>(null); // Ref to scroll to the bottom of the chat area
    const {
        sidebarWidth,
        modPanelWidth,
        isSidebarOpen,
        isMobile,
        isResizing,
        isSidebarToggling,
        toggleSidebar,
        closeSidebar,
        startSidebarResize,
        startModPanelResize,
    } = useResizableLayout(); // Run the useResizableLayout hook to get layout-related state and handlers

    const {
        messages,
        conversations,
        input,
        isQuerying,
        isLoadingConversations,
        isLoadingConversationMessages,
        hasMoreConversationMessages,
        isLoadingMoreConversationMessages,
        conversationsError,
        conversationMessagesError,
        conversationId,
        setInput,
        appendMessage,
        refreshConversations,
        loadConversationMessages,
        loadMoreConversationMessages,
        renameConversation,
        startNewConversation,
        handleQuery,
        handleKeyDown,
    } =
        useChat(); // Run the useChat hook to get chat-related state and handlers
    
    // Document management state and handlers
    const {
        files,
        isLoadingFiles,
        fileListError,
        openTabs,
        activeTab,
        activeTabState,
        handleRefreshDocuments,
        openDocumentTab,
        closeDocumentTab,
        setActiveDocumentTab,
        loadMoreActiveTab,
        invalidateDocumentCache,
        editingDocumentContent,
        isEditingActiveDocument,
        isSavingActiveDocument,
        isActiveDocumentDirty,
        saveError,
        startEditingActiveDocument,
        setActiveEditingDocumentContent,
        cancelEditingActiveDocument,
        saveEditingActiveDocument,
    } = useDocuments(isModificationPanelOpen); // run the useDocuments hook to get document-related state and handlers

    const { selectedFile, isUploading, handleFileSelect, handleUpload, clearFile } = useFileUpload({
        onUploadMessage: (message) => appendMessage({ role: "ai", text: message }),
        onUploadSuccess: async () => {
            invalidateDocumentCache();
            await handleRefreshDocuments();
        },
    });

    // Effect to scroll to the bottom of the chat area whenever messages, querying state, or uploading state changes
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isQuerying, isUploading]);

    useEffect(() => {
        void refreshConversations();
    }, [refreshConversations]);

    // Handler to toggle the modification panel open state
    const handleToggleModificationPanel = () => {
        setIsModificationPanelOpen((prev) => !prev);
    };

    const handleStartRename = (conversationId: string, currentTitle: string) => { // Begins renaming process by setting new/renamed title to be the current title, and storing convo's id
        setRenamingConversationId(conversationId);
        setRenameTitle(currentTitle);
    };

    const handleCancelRename = () => { // If renaming canceled, then clear the renamed title to empty string and clear convo id stored for renaming (reset)
        setRenamingConversationId(null);
        setRenameTitle("");
    };

    const handleSubmitRename = async () => { // When rename is entered/submitted
        if (!renamingConversationId || !renameTitle.trim()) { //return(do nothing) if missing convo id or title is empty (after trimming whitespace via trim())
            return;
        }

        setIsRenamingConversation(true);// Set renaming state to true to disable input and buttons while processing rename
        const success = await renameConversation(renamingConversationId, renameTitle); // Perform the ranem using renameConversation() function passing in id and new title, store if success in "success" variable
        setIsRenamingConversation(false);// Finish rename process, set renaming state back to false to re-enable input and buttons

        if (success) { // If rename was successful, reset renaming state (convo id and new title)
            setRenamingConversationId(null);
            setRenameTitle("");
        }
    };

    const handleLogout = () => {
        localStorage.removeItem("token");
        navigate("/register");
    };

    return (
        <div
            // Root div of the main page, with dynamic classes and styles based on the current state of the layout
            className={`app-root ${isMobile ? "mobile-layout" : ""} ${isSidebarOpen ? "sidebar-open" : "sidebar-closed"} ${isModificationPanelOpen ? "mod-panel-open" : ""} ${isResizing ? "is-resizing" : ""} ${isSidebarToggling ? "is-sidebar-toggling" : ""}`}
            style={
                {
                    "--sidebar-width": `${sidebarWidth}px`,
                    "--mod-panel-width": `${modPanelWidth}px`,
                } as CSSProperties
            }
        >
            <div className={`sidebar-container ${isSidebarOpen ? "open" : "closed"}`}>
                <Sidebar // Render the sidebar component
                    selectedFile={selectedFile}
                    isUploading={isUploading}
                    files={files}
                    isLoadingFiles={isLoadingFiles}
                    fileListError={fileListError}
                    activeTab={activeTab}
                    onFileSelect={handleFileSelect}
                    onUpload={handleUpload}
                    onClearFile={clearFile}
                    onOpenFile={(fileName) => {
                        void openDocumentTab(fileName);
                        setIsModificationPanelOpen(true);
                    }}
                    onRefreshFiles={() => {
                        void handleRefreshDocuments();
                    }}
                />
            </div>

            {/* Allowing this div with resizing sidebar if the sidebar is open and not in mobile view */}
            {!isMobile && isSidebarOpen && (
                <div
                    className="resize-handle resize-handle-sidebar"
                    onMouseDown={(event) => startSidebarResize(event.clientX)}
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="Resize sidebar"
                />
            )}

            {/* Main content which includes the chatbox */}
            <main className="main-content">
                <header className="top-nav">
                    <div className="nav-title-row">
                        {/* Button for opening and closing the sidebar */}
                        <button
                            className="nav-sidebar-toggle"
                            onClick={toggleSidebar}
                            aria-label={isSidebarOpen ? "Hide sidebar" : "Show sidebar"}
                            title={isSidebarOpen ? "Hide sidebar" : "Show sidebar"}
                        >
                            {/* The svg image for the sidebar toggle button (It is a 3 lines menu button) */}
                            <svg
                                width="16"
                                height="16"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                aria-hidden
                            >
                                <line x1="3" y1="6" x2="21" y2="6" />
                                <line x1="3" y1="12" x2="21" y2="12" />
                                <line x1="3" y1="18" x2="21" y2="18" />
                            </svg>
                        </button>
                        <div className="nav-eyebrow">Document chat</div>
                        <div className="nav-title">Ask your documents</div>
                    </div>
                    <div className="nav-actions">
                        <button className="nav-btn" onClick={handleLogout}>
                            Logout
                        </button>
                    </div>
                </header>

                <section className="conversation-switcher" aria-label="Conversation history switcher">
                    <div className="conversation-switcher-header">
                        <div className="conversation-switcher-title">Conversations</div>
                        <button
                            className="nav-btn"
                            onClick={startNewConversation}
                            disabled={isQuerying || isLoadingConversationMessages}
                            type="button"
                        >
                            New chat
                        </button>
                    </div>

                    {isLoadingConversations ? (
                        <div className="conversation-switcher-status">Loading conversations...</div>
                    ) : conversationsError ? (
                        <div className="conversation-switcher-status error">{conversationsError}</div>
                    ) : conversations.length === 0 ? (
                        <div className="conversation-switcher-status">No conversation history yet.</div>
                    ) : (
                        <div className="conversation-chip-list">
                            {conversations.map((conversation) => (
                                <div
                                    key={conversation.conversationId}
                                    className={`conversation-chip-wrapper ${renamingConversationId === conversation.conversationId ? "renaming" : ""}`}
                                >
                                    {renamingConversationId === conversation.conversationId ? (
                                        <div className="conversation-chip-rename">
                                            <input
                                                type="text"
                                                value={renameTitle}
                                                onChange={(e) => setRenameTitle(e.target.value)}
                                                onKeyDown={(e) => {
                                                    if (e.key === "Enter") {
                                                        void handleSubmitRename();
                                                    } else if (e.key === "Escape") {
                                                        handleCancelRename();
                                                    }
                                                }}
                                                autoFocus
                                                disabled={isRenamingConversation}
                                                className="rename-input"
                                            />
                                            <div className="rename-buttons">
                                                <button
                                                    onClick={() => void handleSubmitRename()}
                                                    disabled={isRenamingConversation || !renameTitle.trim()}
                                                    className="rename-btn rename-confirm"
                                                    type="button"
                                                    title="Save (Enter)"
                                                >
                                                    ✓
                                                </button>
                                                <button
                                                    onClick={handleCancelRename}
                                                    disabled={isRenamingConversation}
                                                    className="rename-btn rename-cancel"
                                                    type="button"
                                                    title="Cancel (Esc)"
                                                >
                                                    ✕
                                                </button>
                                            </div>
                                        </div>
                                    ) : (
                                        <>
                                            <button
                                                className={`conversation-chip ${conversationId === conversation.conversationId ? "active" : ""}`}
                                                onClick={() => {
                                                    void loadConversationMessages(conversation.conversationId);
                                                }}
                                                disabled={isLoadingConversationMessages}
                                                type="button"
                                            >
                                                <div className="conversation-chip-title">{conversation.title || "New conversation"}</div>
                                                <div className="conversation-chip-meta">
                                                    <span>{conversation.messageCount ?? 0} msgs</span>
                                                    {conversation.lastMessage?.timestamp && (
                                                        <span className="conversation-chip-timestamp">
                                                            {formatRelativeTime(conversation.lastMessage.timestamp)}
                                                        </span>
                                                    )}
                                                </div>
                                            </button>
                                            <button
                                                className="conversation-rename-trigger"
                                                type="button"
                                                onClick={(event) => {
                                                    event.preventDefault();
                                                    event.stopPropagation();
                                                    handleStartRename(
                                                        conversation.conversationId,
                                                        conversation.title || "New conversation"
                                                    );
                                                }}
                                                disabled={isLoadingConversationMessages}
                                                title="Rename conversation"
                                                aria-label="Rename conversation"
                                            >
                                                <svg
                                                    width="14"
                                                    height="14"
                                                    viewBox="0 0 24 24"
                                                    fill="none"
                                                    stroke="currentColor"
                                                    strokeWidth="2"
                                                    strokeLinecap="round"
                                                    strokeLinejoin="round"
                                                    aria-hidden
                                                >
                                                    <path d="M12 20h9" />
                                                    <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4Z" />
                                                </svg>
                                            </button>
                                        </>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}

                    {conversationMessagesError && (
                        <div className="conversation-switcher-status error">{conversationMessagesError}</div>
                    )}
                </section>

                {/* Chat area of the app (Responsible for showing messages from AI and the user question) */}
                <ChatArea
                    messages={messages}
                    isUploading={isUploading}
                    bottomRef={bottomRef}
                    showLoadOlderMessages={hasMoreConversationMessages}
                    isLoadingOlderMessages={isLoadingMoreConversationMessages}
                    onLoadOlderMessages={loadMoreConversationMessages}
                />

                {/* Chat input area of the app */}
                <ChatInput
                    input={input}
                    isQuerying={isQuerying}
                    isModificationPanelOpen={isModificationPanelOpen}
                    onInputChange={setInput}
                    onInputKeyDown={handleKeyDown}
                    onToggleModificationPanel={handleToggleModificationPanel}
                    onSend={handleQuery}
                />
            </main>

            {/* Allow of resizing of the modification panel if the user is not in mobile view and the modification panel is open*/}
            {!isMobile && isModificationPanelOpen && (
                <div
                    className="resize-handle resize-handle-mod-panel"
                    onMouseDown={(event) => startModPanelResize(event.clientX)}
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="Resize modifications panel"
                />
            )}

            <div className={`mod-panel-container ${isModificationPanelOpen ? "open" : "closed"}`}>
                <ModificationPanel
                    openTabs={openTabs}
                    activeTab={activeTab}
                    activeTabState={activeTabState}
                    isLoadingFiles={isLoadingFiles}
                    editingContent={editingDocumentContent}
                    isEditing={isEditingActiveDocument}
                    isSaving={isSavingActiveDocument}
                    isDirty={isActiveDocumentDirty}
                    saveError={saveError}
                    onRefreshDocuments={handleRefreshDocuments}
                    onClose={() => setIsModificationPanelOpen(false)}
                    onTabSelect={(fileName) => {
                        void setActiveDocumentTab(fileName);
                    }}
                    onTabClose={closeDocumentTab}
                    onLoadMoreActiveTab={loadMoreActiveTab}
                    onStartEditing={startEditingActiveDocument}
                    onEditingContentChange={setActiveEditingDocumentContent}
                    onCancelEditing={cancelEditingActiveDocument}
                    onSaveEditing={() => {
                        void saveEditingActiveDocument();
                    }}
                />
            </div>

            {isMobile && isModificationPanelOpen && (
                <button
                    className="panel-backdrop"
                    onClick={() => setIsModificationPanelOpen(false)}
                    aria-label="Close modifications panel"
                />
            )}

            {isMobile && isSidebarOpen && (
                <button
                    className="panel-backdrop"
                    onClick={closeSidebar}
                    aria-label="Close sidebar"
                />
            )}
        </div>
    );
}
