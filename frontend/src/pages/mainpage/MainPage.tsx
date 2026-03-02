import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
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
    const [isModificationPanelOpen, setIsModificationPanelOpen] = useState(false);
    const [isAiEditModeEnabled, setIsAiEditModeEnabled] = useState(false);
    const [selectedAiFileNames, setSelectedAiFileNames] = useState<string[]>([]);
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

    const { messages, input, isQuerying, setInput, appendMessage, handleQuery } =
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
        isAiEditGenerating,
        aiEditSummary,
        aiEditWarnings,
        aiEditDiffSegments,
        aiEditError,
        aiBatchSelectionMode,
        aiBatchPreviewItems,
        isSavingAiBatch,
        aiBatchSaveMessage,
        aiBatchSaveError,
        hasAiEditProposal,
        requestAiEditPreview,
        acceptAiEditProposal,
        rejectAiEditProposal,
        acceptAiBatchFileProposal,
        rejectAiBatchFileProposal,
        saveAcceptedAiBatchFiles,
        retryFailedAiBatchFiles,
    } = useDocuments(isModificationPanelOpen); // run the useDocuments hook to get document-related state and handlers

    const isAiDocumentEditMode = isAiEditModeEnabled;
    const isManualFileSelectionMode = selectedAiFileNames.length > 0;

    useEffect(() => {
        if (!isAiDocumentEditMode) {
            setSelectedAiFileNames([]);
        }
    }, [isAiDocumentEditMode]);

    useEffect(() => {
        if (!isModificationPanelOpen) {
            setIsAiEditModeEnabled(false);
        }
    }, [isModificationPanelOpen]);

    const handleToggleAiFileSelection = (fileName: string) => {
        setSelectedAiFileNames((previous) =>
            previous.includes(fileName)
                ? previous.filter((name) => name !== fileName)
                : [...previous, fileName]
        );
    };

    const aiSelectionSummary = isAiDocumentEditMode
        ? isManualFileSelectionMode
            ? `${selectedAiFileNames.length} file(s) selected; changes will apply only to selected files.`
            : "No files selected; AI auto-selection mode is active."
        : "Edit mode is not active.";

    const handleToggleAiEditMode = () => {
        if (!isModificationPanelOpen) {
            setIsModificationPanelOpen(true);
            setIsAiEditModeEnabled(true);
            return;
        }

        setIsAiEditModeEnabled((previous) => !previous);
    };

    const handleComposerSend = async () => {
        const textInput = input.trim();

        if (!textInput) {
            return;
        }

        if (isAiDocumentEditMode) {
            appendMessage({ role: "user", text: textInput });
            setInput("");

            const result = await requestAiEditPreview(textInput, {
                selectedFileNames: selectedAiFileNames,
            });

            if (!result.ok) {
                appendMessage({
                    role: "ai",
                    text: `AI edit preview failed: ${result.error ?? "Unknown error"}`,
                });
                return;
            }

            if (result.hasChanges) {
                appendMessage({
                    role: "ai",
                    text: `AI edit preview generated. ${result.summary ?? "Review changes in the edit panel."}`,
                });
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
                    isAiEditModeActive={isAiDocumentEditMode}
                    selectedAiFileNames={selectedAiFileNames}
                    aiSelectionSummary={aiSelectionSummary}
                    onFileSelect={handleFileSelect}
                    onUpload={handleUpload}
                    onClearFile={clearFile}
                    onToggleAiFileSelection={handleToggleAiFileSelection}
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

                {/* Chat area of the app (Responsible for showing messages from AI and the user question) */}
                <ChatArea messages={messages} isUploading={isUploading} bottomRef={bottomRef} />

                {/* Chat input area of the app */}
                <ChatInput
                    input={input}
                    isQuerying={isQuerying || isAiEditGenerating}
                    isAiEditModeActive={isAiDocumentEditMode}
                    aiSelectionSummary={aiSelectionSummary}
                    onInputChange={setInput}
                    onInputKeyDown={handleComposerKeyDown}
                    onToggleAiEditMode={handleToggleAiEditMode}
                    onSend={() => {
                        void handleComposerSend();
                    }}
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
                    onClose={() => {
                        setIsModificationPanelOpen(false);
                        setIsAiEditModeEnabled(false);
                    }}
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
                    aiEditSummary={aiEditSummary}
                    aiEditWarnings={aiEditWarnings}
                    aiEditDiffSegments={aiEditDiffSegments}
                    aiEditError={aiEditError}
                    aiBatchSelectionMode={aiBatchSelectionMode}
                    aiBatchPreviewItems={aiBatchPreviewItems}
                    isSavingAiBatch={isSavingAiBatch}
                    aiBatchSaveMessage={aiBatchSaveMessage}
                    aiBatchSaveError={aiBatchSaveError}
                    hasAiEditProposal={hasAiEditProposal}
                    isAiEditGenerating={isAiEditGenerating}
                    onAcceptAiEdit={acceptAiEditProposal}
                    onRejectAiEdit={rejectAiEditProposal}
                    onAcceptAiBatchFile={acceptAiBatchFileProposal}
                    onRejectAiBatchFile={rejectAiBatchFileProposal}
                    onSaveAcceptedBatchFiles={() => {
                        void (async () => {
                            const result = await saveAcceptedAiBatchFiles();
                            if (result.closeAfterSave) {
                                setIsModificationPanelOpen(false);
                                setIsAiEditModeEnabled(false);
                            }
                        })();
                    }}
                    onRetryFailedBatchFiles={retryFailedAiBatchFiles}
                />
            </div>

            {isMobile && isModificationPanelOpen && (
                <button
                    className="panel-backdrop"
                    onClick={() => {
                        setIsModificationPanelOpen(false);
                        setIsAiEditModeEnabled(false);
                    }}
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
