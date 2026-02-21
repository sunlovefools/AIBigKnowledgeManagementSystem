import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
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
<<<<<<< HEAD

    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState("");

    // Sidebar State
    const [isSidebarOpen, setIsSidebarOpen] = useState(true);

    // Modification Panel State
=======
>>>>>>> 39a6de5aeabb627c68f8620bc5670f4dfe122397
    const [isModificationPanelOpen, setIsModificationPanelOpen] = useState(false);
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

    const { messages, input, isQuerying, setInput, appendMessage, handleQuery, handleKeyDown } =
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

    // Handler to toggle the modification panel open state
    const handleToggleModificationPanel = () => {
<<<<<<< HEAD
        setIsModificationPanelOpen(!isModificationPanelOpen);
    };

    const handleToggleSidebar = () => {
        setIsSidebarOpen(!isSidebarOpen);
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
=======
        setIsModificationPanelOpen((prev) => !prev);
>>>>>>> 39a6de5aeabb627c68f8620bc5670f4dfe122397
    };

    const handleLogout = () => {
        localStorage.removeItem("token");
        navigate("/register");
    };

    return (
<<<<<<< HEAD
        <div className={`app-root ${isSidebarOpen ? "" : "sidebar-collapsed"} ${isModificationPanelOpen ? "with-mod-panel" : ""}`}>
            {isSidebarOpen && (
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
=======
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
>>>>>>> 39a6de5aeabb627c68f8620bc5670f4dfe122397
            )}

            {/* Main content which includes the chatbox */}
            <main className="main-content">
                <header className="top-nav">
<<<<<<< HEAD
                    <div className="nav-left">
                        <button
                            className={`sidebar-toggle-btn ${isSidebarOpen ? "active" : ""}`}
                            onClick={handleToggleSidebar}
                            aria-label="Toggle sidebar"
                            title={isSidebarOpen ? "Hide files" : "Show files"}
                        >
                            <svg
                                width="20"
                                height="20"
=======
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
>>>>>>> 39a6de5aeabb627c68f8620bc5670f4dfe122397
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
<<<<<<< HEAD
                            >
                                <line x1="3" y1="12" x2="21" y2="12"></line>
                                <line x1="3" y1="6" x2="21" y2="6"></line>
                                <line x1="3" y1="18" x2="21" y2="18"></line>
                            </svg>
                        </button>
                        <div>
                            <div className="nav-eyebrow">Document chat</div>
                            <div className="nav-title">Ask your documents</div>
                        </div>
=======
                                aria-hidden
                            >
                                <line x1="3" y1="6" x2="21" y2="6" />
                                <line x1="3" y1="12" x2="21" y2="12" />
                                <line x1="3" y1="18" x2="21" y2="18" />
                            </svg>
                        </button>
                        <div className="nav-eyebrow">Document chat</div>
                        <div className="nav-title">Ask your documents</div>
>>>>>>> 39a6de5aeabb627c68f8620bc5670f4dfe122397
                    </div>
                    <div className="nav-actions">
                        <button className="nav-btn" onClick={handleLogout}>
                            Logout
                        </button>
                    </div>
                </header>

                {/* Chat area of the app (Responsible for showing messages from AI and the user question) */}
                <ChatArea messages={messages} isUploading={isUploading} bottomRef={bottomRef} />

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
