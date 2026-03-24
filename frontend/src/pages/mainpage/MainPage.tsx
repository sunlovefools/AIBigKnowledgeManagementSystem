import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
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
import { useDocuments } from "./hooks/documents/useDocuments";
import { useFileUpload } from "./hooks/useFileUpload";
import { useResizableLayout } from "./hooks/useResizableLayout";
import type { AgentProposal, HighlightedSelection, PendingModificationNavItem } from "./types";
import type { ModificationProgressEvent } from "./hooks/documents/api/documentsApi";

function getProposalKey(proposal: AgentProposal): string {
    return `${proposal.parentId}-${proposal.selectionStart ?? "full"}`;
}

export default function MainPage() {
    const navigate = useNavigate();
    const [isModificationPanelOpen, setIsModificationPanelOpen] = useState(false);
    const [renamingConversationId, setRenamingConversationId] = useState<string | null>(null);
    const [renameTitle, setRenameTitle] = useState("");
    const [isRenamingConversation, setIsRenamingConversation] = useState(false);
    const [isEditMode, setIsEditMode] = useState(false);
    const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(new Set());
    const [highlightedSelection, setHighlightedSelection] = useState<HighlightedSelection | null>(null);
    const [selectionError, setSelectionError] = useState<string | null>(null);
    const [pendingDeleteFile, setPendingDeleteFile] = useState<{ fileId: string; fileName: string } | null>(null);
    const [focusedProposalKey, setFocusedProposalKey] = useState<string | null>(null);

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
        startProgressMessage,
        pushProgressStep,
        finishProgressMessage,
        refreshConversations,
        loadConversationMessages,
        loadMoreConversationMessages,
        renameConversation,
        startNewConversation,
        handleQuery,
    } =
        useChat(); // Run the useChat hook to get chat-related state and handlers

    const {
        files,
        isLoadingFiles,
        fileListError,
        deletingFileId,
        openTabs,
        activeTab,
        activeTabData,
        activeTabAsync,
        handleRefreshDocuments,
        deleteFile,
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
        agentRejectedIds,
        agentError,
        agentIntention,
        requestAgentEditPreview,
        requestSelectionEditPreview,
        acceptAgentProposal,
        rejectAgentProposal,
        undoAgentProposal,
        clearAgentState,
        ensureFileFullyLoaded,
    } = useDocuments(isModificationPanelOpen);

    const handleToggleFileSelection = useCallback((fileId: string) => {
        setSelectedFileIds((prev) => {
            const next = new Set(prev);
            if (next.has(fileId)) next.delete(fileId);
            else next.add(fileId);
            return next;
        });
    }, []);

    // Handler to send the user's query edit query either to selection-based edit or agent-based edit
    const handleComposerSend = async () => {
        const textInput = input.trim();
        if (!textInput) return;

        if (isEditMode) {
            appendMessage({ role: "user", text: textInput });
            setInput("");
            const selectedRange = highlightedSelection;
            const progressMessageId = startProgressMessage(
                selectedRange ? "selection" : "agentic",
                selectedRange ? "Selection edit preview started." : "Agentic modification started."
            );
            const onProgress = (progress: ModificationProgressEvent) => {
                pushProgressStep(progressMessageId, progress);
            };
            let progressStatus: "completed" | "failed" = "failed";
            try {
                const result = selectedRange // If there is a highlightSelection then requestSelectionEditPreview
                    // TODO: We should chanege the name without Preview once it is done testing
                    ? await requestSelectionEditPreview(textInput, selectedRange, onProgress)
                    : await requestAgentEditPreview(
                        textInput,
                        selectedFileIds.size > 0 ? Array.from(selectedFileIds) : null,
                        onProgress
                    );
                appendMessage({
                    role: "ai",
                        text: result.ok
                            ? result.summary ?? "Review the proposals in the edit panel."
                            : `Edit failed: ${result.error ?? "Unknown error"}`,
                });
                progressStatus = result.ok ? "completed" : "failed";
                if (result.ok && selectedRange) {
                    setHighlightedSelection(null);
                    setSelectionError(null);
                }
            } finally {
                finishProgressMessage(progressMessageId, progressStatus);
            }
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

    const handleConfirmDeleteFile = useCallback(async () => {
        if (!pendingDeleteFile) return;
        const { fileId, fileName } = pendingDeleteFile;
        const result = await deleteFile(fileId);
        if (!result.ok || !result.data) {
            appendMessage({
                role: "ai",
                text: result.error ?? `Failed to delete "${fileName}".`,
            });
            return;
        }

        setSelectedFileIds((prev) => {
            if (!prev.has(fileId)) return prev;
            const next = new Set(prev);
            next.delete(fileId);
            return next;
        });

        const warningText = result.data.warnings.length > 0
            ? ` Warnings: ${result.data.warnings.join(" ")}`
            : "";
        appendMessage({
            role: "ai",
            text:
                `Deleted "${result.data.fileName}" from the knowledge base ` +
                `(${result.data.deletedParentChunks} parent chunks, ${result.data.deletedChildChunks} child chunks). ` +
                `S3 cleanup: ${result.data.s3Status}.${warningText}`,
        });
        setPendingDeleteFile(null);
    }, [appendMessage, deleteFile, pendingDeleteFile]);

    const handleRequestDeleteFile = useCallback((fileId: string) => {
        const fileName = files.find((file) => file.fileId === fileId)?.fileName ?? fileId;
        setPendingDeleteFile({ fileId, fileName });
    }, [files]);

    const handleCancelDeleteFile = useCallback(() => {
        if (deletingFileId) return;
        setPendingDeleteFile(null);
    }, [deletingFileId]);

    const { selectedFile, isUploading, handleFileSelect, handleUpload, clearFile } = useFileUpload({
        onUploadMessage: (message) => appendMessage({ role: "ai", text: message }),
        onUploadSuccess: async () => {
            invalidateDocumentCache();
            await handleRefreshDocuments();
        },
    });

    const activeChunkSignature = activeTabData?.chunks
        .map((chunk) => `${chunk.parentId}:${chunk.size}`)
        .join("|") ?? "";
    const hasSelectedDocument = Boolean(activeTab);
    const isDesktopWorkspaceActive = !isMobile && isModificationPanelOpen && hasSelectedDocument;
    const chatEmptyStateMode = isEditMode && !hasSelectedDocument ? "no-document" : "welcome";
    const activeFileName = activeTab
        ? files.find((file) => file.fileId === activeTab)?.fileName ?? activeTab
        : "No file selected";
    const isDeletingActiveFile = Boolean(activeTab && deletingFileId === activeTab);

    const pendingModificationItems = useMemo<PendingModificationNavItem[]>(() => {
        const grouped = new Map<string, PendingModificationNavItem>();
        for (const proposal of agentProposals) {
            if (agentAcceptedMap.has(proposal.parentId) || agentRejectedIds.has(proposal.parentId)) {
                continue;
            }

            const existing = grouped.get(proposal.fileId);
            if (existing) {
                existing.pendingCount += 1;
                continue;
            }

            grouped.set(proposal.fileId, {
                fileId: proposal.fileId,
                fileName: proposal.fileName,
                pendingCount: 1,
                targetProposalKey: getProposalKey(proposal),
            });
        }
        return Array.from(grouped.values());
    }, [agentAcceptedMap, agentProposals, agentRejectedIds]);

    const handleNavigateToModification = useCallback(async (fileId: string, proposalKey: string) => {
        setIsModificationPanelOpen(true);
        setIsEditMode(true);

        if (activeTab !== fileId) {
            await openDocumentTab(fileId);
        }
        setFocusedProposalKey(proposalKey);
    }, [activeTab, openDocumentTab]);

    const renderChatWorkspace = (emptyStateMode: "welcome" | "no-document") => (
        <div className="chat-stage-shell">
            <ChatArea
                messages={messages}
                isUploading={isUploading}
                bottomRef={bottomRef}
                emptyStateMode={emptyStateMode}
                showLoadOlderMessages={hasMoreConversationMessages}
                isLoadingOlderMessages={isLoadingMoreConversationMessages}
                onLoadOlderMessages={loadMoreConversationMessages}
            />

            <ChatInput
                input={input}
                isQuerying={isQuerying || isAgentGenerating}
                isModificationPanelOpen={isModificationPanelOpen}
                isEditMode={isEditMode}
                highlightedSelection={highlightedSelection}
                pendingModificationItems={pendingModificationItems}
                onInputChange={setInput}
                onInputKeyDown={handleComposerKeyDown}
                onToggleModificationPanel={handleToggleModificationPanel}
                onClearHighlightedSelection={clearHighlightedSelection}
                onNavigateToModification={(fileId, proposalKey) => { void handleNavigateToModification(fileId, proposalKey); }}
                onSend={() => { void handleComposerSend(); }}
            />
        </div>
    );

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isQuerying, isUploading]);

    useEffect(() => {
        void refreshConversations();
    }, [refreshConversations]);

    useEffect(() => {
        if (!isEditMode || isEditingActiveDocument) {
            setHighlightedSelection(null);
            setSelectionError(null);
        }
    }, [isEditMode, isEditingActiveDocument]);

    useEffect(() => {
        setHighlightedSelection(null);
        setSelectionError(null);
    }, [activeTab]);

    useEffect(() => {
        setHighlightedSelection(null);
        setSelectionError(null);
    }, [activeChunkSignature]);

    useEffect(() => {
        if (!agentProposals.length) return;

        let isCancelled = false;
        const fileIds = Array.from(new Set(agentProposals.map((proposal) => proposal.fileId)));
        const hydrateProposalFiles = async () => {
            for (const fileId of fileIds) {
                if (isCancelled) return;
                await ensureFileFullyLoaded(fileId);
            }
        };

        void hydrateProposalFiles();
        return () => {
            isCancelled = true;
        };
    }, [agentProposals, ensureFileFullyLoaded]);

    const handleSelectionChange = useCallback((selection: HighlightedSelection | null) => {
        setHighlightedSelection(selection);
        if (selection) {
            setSelectionError(null);
        }
    }, []);

    const handleSelectionErrorChange = useCallback((message: string | null) => {
        setSelectionError(message);
        if (message) {
            setHighlightedSelection(null);
        }
    }, []);

    const clearHighlightedSelection = useCallback(() => {
        window.getSelection()?.removeAllRanges();
        setHighlightedSelection(null);
        setSelectionError(null);
    }, []);

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
            clearHighlightedSelection();
            setFocusedProposalKey(null);
        }
    };

    const handleCloseModificationPanel = () => {
        setIsModificationPanelOpen(false);
        setIsEditMode(false);
        setSelectedFileIds(new Set());
        clearHighlightedSelection();
        setFocusedProposalKey(null);
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

    const modificationPanel = (
        <ModificationPanel
            files={files}
            openTabs={openTabs}
            activeTab={activeTab}
            activeTabData={activeTabData}
            activeTabAsync={activeTabAsync}
            isLoadingFiles={isLoadingFiles}
            deletingFileId={deletingFileId}
            editingContent={editingDocumentContent}
            isEditing={isEditingActiveDocument}
            isSaving={isSavingActiveDocument}
            isDirty={isActiveDocumentDirty}
            saveError={saveError}
            isEditMode={isEditMode}
            selectedFileIds={selectedFileIds}
            highlightedSelection={highlightedSelection}
            selectionError={selectionError}
            onRefreshDocuments={handleRefreshDocuments}
            onClose={handleCloseModificationPanel}
            onTabSelect={(fileId) => { void setActiveDocumentTab(fileId); }}
            onTabClose={closeDocumentTab}
            onLoadMoreActiveTab={loadMoreActiveTab}
            onStartEditing={startEditingActiveDocument}
            onDeleteActiveFile={() => { if (activeTab) handleRequestDeleteFile(activeTab); }}
            onEditingContentChange={setActiveEditingDocumentContent}
            onCancelEditing={cancelEditingActiveDocument}
            onSaveEditing={() => { void saveEditingActiveDocument(); }}
            onHighlightedSelectionChange={handleSelectionChange}
            onSelectionErrorChange={handleSelectionErrorChange}
            isAgentGenerating={isAgentGenerating}
            agentProposals={agentProposals}
            agentAcceptedMap={agentAcceptedMap}
            agentRejectedIds={agentRejectedIds}
            agentError={agentError}
            agentIntention={agentIntention}
            onAcceptAgentProposal={(proposal) => acceptAgentProposal(proposal)}
            onRejectAgentProposal={rejectAgentProposal}
            onUndoAgentProposal={undoAgentProposal}
            onClearAgentProposals={clearAgentState}
            focusedProposalKey={focusedProposalKey}
            onFocusedProposalHandled={() => setFocusedProposalKey(null)}
        />
    );

    const desktopModificationPanel = (
        <ModificationPanel
            files={files}
            openTabs={openTabs}
            activeTab={activeTab}
            activeTabData={activeTabData}
            activeTabAsync={activeTabAsync}
            isLoadingFiles={isLoadingFiles}
            deletingFileId={deletingFileId}
            editingContent={editingDocumentContent}
            isEditing={isEditingActiveDocument}
            isSaving={isSavingActiveDocument}
            isDirty={isActiveDocumentDirty}
            saveError={saveError}
            isEditMode={isEditMode}
            selectedFileIds={selectedFileIds}
            highlightedSelection={highlightedSelection}
            selectionError={selectionError}
            hideTabs
            hideHeader
            hideDocumentToolbar
            onRefreshDocuments={handleRefreshDocuments}
            onClose={handleCloseModificationPanel}
            onTabSelect={(fileId) => { void setActiveDocumentTab(fileId); }}
            onTabClose={closeDocumentTab}
            onLoadMoreActiveTab={loadMoreActiveTab}
            onStartEditing={startEditingActiveDocument}
            onDeleteActiveFile={() => { if (activeTab) handleRequestDeleteFile(activeTab); }}
            onEditingContentChange={setActiveEditingDocumentContent}
            onCancelEditing={cancelEditingActiveDocument}
            onSaveEditing={() => { void saveEditingActiveDocument(); }}
            onHighlightedSelectionChange={handleSelectionChange}
            onSelectionErrorChange={handleSelectionErrorChange}
            isAgentGenerating={isAgentGenerating}
            agentProposals={agentProposals}
            agentAcceptedMap={agentAcceptedMap}
            agentRejectedIds={agentRejectedIds}
            agentError={agentError}
            agentIntention={agentIntention}
            onAcceptAgentProposal={(proposal) => acceptAgentProposal(proposal)}
            onRejectAgentProposal={rejectAgentProposal}
            onUndoAgentProposal={undoAgentProposal}
            onClearAgentProposals={clearAgentState}
            focusedProposalKey={focusedProposalKey}
            onFocusedProposalHandled={() => setFocusedProposalKey(null)}
        />
    );

    return (
        <div
            className={`app-root ${isMobile ? "mobile-layout" : ""} ${isSidebarOpen ? "sidebar-open" : "sidebar-closed"} ${isModificationPanelOpen ? "mod-panel-open" : ""} ${isResizing ? "is-resizing" : ""} ${isSidebarToggling ? "is-sidebar-toggling" : ""}`}
            style={{
                "--sidebar-width": `${sidebarWidth}px`,
                "--mod-panel-width": `${modPanelWidth}px`,
                "--assistant-stage-width": `${modPanelWidth}px`,
            } as CSSProperties}
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

                {isMobile ? (
                    renderChatWorkspace(chatEmptyStateMode)
                ) : isDesktopWorkspaceActive ? (
                    <div className="desktop-edit-workspace" aria-live="polite">
                        <section className="desktop-modification-stage">
                            <div className="desktop-stage-tabs" role="tablist" aria-label="Opened documents">
                                {openTabs.length === 0 ? (
                                    <div className="mod-panel-tabs-empty">Open a file from the sidebar to view full content.</div>
                                ) : (
                                    openTabs.map((fileId) => {
                                        const fileName = files.find((file) => file.fileId === fileId)?.fileName ?? fileId;
                                        return (
                                            <div key={fileId} className={`mod-panel-tab ${activeTab === fileId ? "active" : ""}`}>
                                                <button
                                                    className="mod-panel-tab-label"
                                                    onClick={() => { void setActiveDocumentTab(fileId); }}
                                                    type="button"
                                                >
                                                    {fileName}
                                                </button>
                                                <button
                                                    className="mod-panel-tab-close"
                                                    onClick={() => closeDocumentTab(fileId)}
                                                    aria-label={`Close ${fileName}`}
                                                    type="button"
                                                >
                                                    x
                                                </button>
                                            </div>
                                        );
                                    })
                                )}
                            </div>

                            <div className="desktop-stage-header">
                                <div className="desktop-stage-header-main">
                                    <h3 className="desktop-stage-file-name">{activeFileName}</h3>
                                    {isEditMode && (
                                        <span className="mod-panel-edit-mode-badge">
                                            Edit - {selectedFileIds.size > 0 ? `${selectedFileIds.size} file(s) selected` : "All files"}
                                        </span>
                                    )}
                                </div>

                                <div className="desktop-stage-header-actions">
                                    {isEditingActiveDocument ? (
                                        <>
                                            <span className="mod-panel-editing-indicator">Editing mode</span>
                                            <div className="document-action-group">
                                                <button
                                                    className="save-btn"
                                                    type="button"
                                                    onClick={() => { void saveEditingActiveDocument(); }}
                                                    disabled={isSavingActiveDocument || !isActiveDocumentDirty}
                                                >
                                                    {isSavingActiveDocument ? "Saving..." : "Save"}
                                                </button>
                                                <button
                                                    className="cancel-btn"
                                                    type="button"
                                                    onClick={cancelEditingActiveDocument}
                                                    disabled={isSavingActiveDocument}
                                                >
                                                    Cancel
                                                </button>
                                            </div>
                                        </>
                                    ) : (
                                        <div className="document-action-group">
                                            <button
                                                className="edit-btn"
                                                type="button"
                                                onClick={startEditingActiveDocument}
                                                disabled={isSavingActiveDocument || isDeletingActiveFile || Boolean(activeTabAsync?.isLoading)}
                                            >
                                                Edit
                                            </button>
                                            <button
                                                className="delete-btn"
                                                type="button"
                                                onClick={() => { if (activeTab) handleRequestDeleteFile(activeTab); }}
                                                disabled={isSavingActiveDocument || isDeletingActiveFile || Boolean(activeTabAsync?.isLoading)}
                                            >
                                                {isDeletingActiveFile ? "Deleting..." : "Delete"}
                                            </button>
                                        </div>
                                    )}

                                    <button
                                        className="mod-panel-refresh-btn"
                                        onClick={handleRefreshDocuments}
                                        disabled={isLoadingFiles}
                                        aria-label="Refresh documents"
                                        title="Refresh from database"
                                    >
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                            <polyline points="23 4 23 10 17 10"></polyline>
                                            <polyline points="1 20 1 14 7 14"></polyline>
                                            <path d="M3.51 9a9 9 0 0 1 14.85-3.36M20.49 15a9 9 0 0 1-14.85 3.36"></path>
                                        </svg>
                                    </button>
                                    <button className="mod-panel-close-btn" onClick={handleCloseModificationPanel} aria-label="Close modifications panel">
                                        x
                                    </button>
                                </div>
                            </div>

                            {desktopModificationPanel}
                        </section>
                        <div
                            className="resize-handle resize-handle-workspace"
                            onMouseDown={(event) => startModPanelResize(event.clientX)}
                            role="separator"
                            aria-orientation="vertical"
                            aria-label="Resize editor and chat panels"
                        />
                        <section className="desktop-assistant-stage">
                            {renderChatWorkspace("welcome")}
                        </section>
                    </div>
                ) : (
                    <div className="desktop-chat-focus-stage" aria-live="polite">
                        {renderChatWorkspace(chatEmptyStateMode)}
                    </div>
                )}
            </main>

            {isMobile && (
                <div className={`mod-panel-container ${isModificationPanelOpen ? "open" : "closed"}`}>
                    {modificationPanel}
                </div>
            )}

            {isMobile && isModificationPanelOpen && (
                <button className="panel-backdrop" onClick={handleCloseModificationPanel} aria-label="Close modifications panel" />
            )}
            {isMobile && isSidebarOpen && (
                <button className="panel-backdrop" onClick={closeSidebar} aria-label="Close sidebar" />
            )}

            {pendingDeleteFile && (
                <div
                    className="delete-confirm-overlay"
                    onClick={handleCancelDeleteFile}
                    role="presentation"
                >
                    <div
                        className="delete-confirm-dialog"
                        onClick={(event) => event.stopPropagation()}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="delete-confirm-title"
                    >
                        <div className="delete-confirm-eyebrow">Delete file</div>
                        <h3 id="delete-confirm-title" className="delete-confirm-title">
                            Remove "{pendingDeleteFile.fileName}"?
                        </h3>
                        <p className="delete-confirm-text">
                            This removes the file from the Knowledge Base.
                        </p>
                        <div className="delete-confirm-actions">
                            <button
                                className="delete-confirm-cancel"
                                type="button"
                                onClick={handleCancelDeleteFile}
                                disabled={Boolean(deletingFileId)}
                            >
                                Keep file
                            </button>
                            <button
                                className="delete-confirm-submit"
                                type="button"
                                onClick={() => { void handleConfirmDeleteFile(); }}
                                disabled={Boolean(deletingFileId)}
                            >
                                {deletingFileId ? "Deleting..." : "Delete"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
