import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
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

export default function MainPage() {
    const navigate = useNavigate();
    const [isModificationPanelOpen, setIsModificationPanelOpen] = useState(false);
    const [isEditMode, setIsEditMode] = useState(false);
    const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(new Set());

    const bottomRef = useRef<HTMLDivElement | null>(null);

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
    } = useResizableLayout();

    const { messages, input, isQuerying, setInput, appendMessage, handleQuery } = useChat();

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
        isAgentGenerating,
        agentProposals,
        agentAcceptedMap,
        agentSavedIds,
        agentRejectedIds,
        agentSavingIds,
        agentError,
        agentIntention,
        requestAgentEditPreview,
        acceptAgentProposal,
        saveAgentProposal,
        rejectAgentProposal,
        clearAgentState,
    } = useDocuments(isModificationPanelOpen);

    const handleToggleFileSelection = useCallback((fileId: string) => {
        setSelectedFileIds((prev) => {
            const next = new Set(prev);
            if (next.has(fileId)) next.delete(fileId);
            else next.add(fileId);
            return next;
        });
    }, []);

    const handleComposerSend = async () => {
        const textInput = input.trim();
        if (!textInput) return;

        if (isEditMode) {
            appendMessage({ role: "user", text: textInput });
            setInput("");
            const fileIds = selectedFileIds.size > 0 ? Array.from(selectedFileIds) : null;
            const result = await requestAgentEditPreview(textInput, fileIds);
            appendMessage({
                role: "ai",
                text: result.ok
                    ? result.summary ?? "Review the proposals in the edit panel."
                    : `Edit failed: ${result.error ?? "Unknown error"}`,
            });
            return;
        }

        await handleQuery();
    };

    const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void handleComposerSend();
        }
    };

    const { selectedFile, isUploading, handleFileSelect, handleUpload, clearFile } = useFileUpload({
        onUploadMessage: (message) => appendMessage({ role: "ai", text: message }),
        onUploadSuccess: async () => {
            invalidateDocumentCache();
            await handleRefreshDocuments();
        },
    });

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isQuerying, isUploading]);

    // Pencil button:
    // - Panel closed           → open panel in edit mode
    // - Panel open, view mode  → switch to edit mode (don't close panel)
    // - Panel open, edit mode  → exit edit mode back to view mode
    const handleToggleModificationPanel = () => {
        if (!isModificationPanelOpen) {
            setIsModificationPanelOpen(true);
            setIsEditMode(true);
        } else if (!isEditMode) {
            setIsEditMode(true);
        } else {
            setIsEditMode(false);
            setSelectedFileIds(new Set());
        }
    };

    const handleCloseModificationPanel = () => {
        setIsModificationPanelOpen(false);
        setIsEditMode(false);
        setSelectedFileIds(new Set());
    };

    const handleLogout = () => {
        localStorage.removeItem("token");
        navigate("/register");
    };

    return (
        <div
            className={`app-root ${isMobile ? "mobile-layout" : ""} ${isSidebarOpen ? "sidebar-open" : "sidebar-closed"} ${isModificationPanelOpen ? "mod-panel-open" : ""} ${isResizing ? "is-resizing" : ""} ${isSidebarToggling ? "is-sidebar-toggling" : ""}`}
            style={{ "--sidebar-width": `${sidebarWidth}px`, "--mod-panel-width": `${modPanelWidth}px` } as CSSProperties}
        >
            <div className={`sidebar-container ${isSidebarOpen ? "open" : "closed"}`}>
                <Sidebar
                    selectedFile={selectedFile}
                    isUploading={isUploading}
                    files={files}
                    isLoadingFiles={isLoadingFiles}
                    fileListError={fileListError}
                    activeTab={activeTab}
                    isEditMode={isEditMode}
                    selectedFileIds={selectedFileIds}
                    onToggleFileSelection={handleToggleFileSelection}
                    onFileSelect={handleFileSelect}
                    onUpload={handleUpload}
                    onClearFile={clearFile}
                    onOpenFile={(fileId) => {
                        void openDocumentTab(fileId);
                        setIsModificationPanelOpen(true);
                    }}
                    onRefreshFiles={() => { void handleRefreshDocuments(); }}
                />
            </div>

            {!isMobile && isSidebarOpen && (
                <div
                    className="resize-handle resize-handle-sidebar"
                    onMouseDown={(event) => startSidebarResize(event.clientX)}
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="Resize sidebar"
                />
            )}

            <main className="main-content">
                <header className="top-nav">
                    <div className="nav-title-row">
                        <button
                            className="nav-sidebar-toggle"
                            onClick={toggleSidebar}
                            aria-label={isSidebarOpen ? "Hide sidebar" : "Show sidebar"}
                            title={isSidebarOpen ? "Hide sidebar" : "Show sidebar"}
                        >
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                <line x1="3" y1="6" x2="21" y2="6" />
                                <line x1="3" y1="12" x2="21" y2="12" />
                                <line x1="3" y1="18" x2="21" y2="18" />
                            </svg>
                        </button>
                        <div className="nav-eyebrow">Document chat</div>
                        <div className="nav-title">Ask your documents</div>
                    </div>
                    <div className="nav-actions">
                        <button className="nav-btn" onClick={handleLogout}>Logout</button>
                    </div>
                </header>

                <ChatArea messages={messages} isUploading={isUploading} bottomRef={bottomRef} />

                <ChatInput
                    input={input}
                    isQuerying={isQuerying || isAgentGenerating}
                    isModificationPanelOpen={isModificationPanelOpen}
                    isEditMode={isEditMode}
                    onInputChange={setInput}
                    onInputKeyDown={handleComposerKeyDown}
                    onToggleModificationPanel={handleToggleModificationPanel}
                    onSend={() => { void handleComposerSend(); }}
                />
            </main>

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
                    files={files}
                    openTabs={openTabs}
                    activeTab={activeTab}
                    activeTabState={activeTabState}
                    isLoadingFiles={isLoadingFiles}
                    editingContent={editingDocumentContent}
                    isEditing={isEditingActiveDocument}
                    isSaving={isSavingActiveDocument}
                    isDirty={isActiveDocumentDirty}
                    saveError={saveError}
                    isEditMode={isEditMode}
                    selectedFileIds={selectedFileIds}
                    onRefreshDocuments={handleRefreshDocuments}
                    onClose={handleCloseModificationPanel}
                    onTabSelect={(fileId) => { void setActiveDocumentTab(fileId); }}
                    onTabClose={closeDocumentTab}
                    onLoadMoreActiveTab={loadMoreActiveTab}
                    onStartEditing={startEditingActiveDocument}
                    onEditingContentChange={setActiveEditingDocumentContent}
                    onCancelEditing={cancelEditingActiveDocument}
                    onSaveEditing={() => { void saveEditingActiveDocument(); }}
                    isAgentGenerating={isAgentGenerating}
                    agentProposals={agentProposals}
                    agentAcceptedMap={agentAcceptedMap}
                    agentSavedIds={agentSavedIds}
                    agentRejectedIds={agentRejectedIds}
                    agentSavingIds={agentSavingIds}
                    agentError={agentError}
                    agentIntention={agentIntention}
                    onAcceptAgentProposal={(proposal) => acceptAgentProposal(proposal)}
                    onSaveAgentProposal={(proposal) => { void saveAgentProposal(proposal); }}
                    onRejectAgentProposal={rejectAgentProposal}
                    onClearAgentProposals={clearAgentState}
                />
            </div>

            {isMobile && isModificationPanelOpen && (
                <button className="panel-backdrop" onClick={handleCloseModificationPanel} aria-label="Close modifications panel" />
            )}
            {isMobile && isSidebarOpen && (
                <button className="panel-backdrop" onClick={closeSidebar} aria-label="Close sidebar" />
            )}
        </div>
    );
}
