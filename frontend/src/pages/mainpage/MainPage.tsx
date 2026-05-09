import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";

import "./MainPage.css";
import "highlight.js/styles/github.css";
import GlobalSidebar from "../../components/GlobalSidebar";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import ChatInput from "./components/ChatInput";
import ModificationPanel from "./components/ModificationPanel";
import ScopePicker from "./components/ScopePicker";
import { useChat } from "./hooks/useChat";
import { useDocuments } from "./hooks/documents/useDocuments";
import { useResizableLayout } from "./hooks/useResizableLayout";
import { consumeConversationLaunch } from "./conversationLaunch";
import { useUploadQueue } from "../../upload/uploadQueueState";
import type { AgentProposal, ChatScope, HighlightedSelection, PendingModificationNavItem } from "./types";
import type { ModificationAgentMode, ModificationProgressEvent } from "./hooks/documents/api/documentsApi";

function getProposalKey(proposal: AgentProposal): string {
    return `${proposal.parentId}-${proposal.selectionStart ?? "full"}`;
}

type MobileWorkspace = "chat" | "files" | "document";
const MODIFICATION_AGENT_MODE_STORAGE_KEY = "modificationAgentMode";

function loadModificationAgentMode(): ModificationAgentMode {
    if (typeof window === "undefined") return "workflow";
    return window.localStorage.getItem(MODIFICATION_AGENT_MODE_STORAGE_KEY) === "skills"
        ? "skills"
        : "workflow";
}

export default function ConversationPage() {
    const [isModificationPanelOpen, setIsModificationPanelOpen] = useState(false);
    const [isModificationPanelClosing, setIsModificationPanelClosing] = useState(false);
    const [mobileWorkspace, setMobileWorkspace] = useState<MobileWorkspace>("chat");
    // Controls the collapsible "current chat title" menu shown inside chat-stage-shell.
    const [isConversationMenuOpen, setIsConversationMenuOpen] = useState(false);
    const [renamingConversationId, setRenamingConversationId] = useState<string | null>(null);
    const [renameTitle, setRenameTitle] = useState("");
    const [isRenamingConversation, setIsRenamingConversation] = useState(false);
    const [deletingConversationId, setDeletingConversationId] = useState<string | null>(null);
    const [pendingDeleteConversation, setPendingDeleteConversation] = useState<{ conversationId: string; title: string } | null>(null);
    const [isEditMode, setIsEditMode] = useState(false);
    const [desktopFileNameDraft, setDesktopFileNameDraft] = useState("");
    const [desktopFileNameError, setDesktopFileNameError] = useState<string | null>(null);
    const [isSavingDesktopFileName, setIsSavingDesktopFileName] = useState(false);
    const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(new Set());
    const [highlightedSelection, setHighlightedSelection] = useState<HighlightedSelection | null>(null);
    const [selectionError, setSelectionError] = useState<string | null>(null);
    const [pendingDeleteFile, setPendingDeleteFile] = useState<{ fileId: string; fileName: string } | null>(null);
    const [focusedProposalKey, setFocusedProposalKey] = useState<string | null>(null);
    const [chatScope, setChatScope] = useState<ChatScope>({ type: "all_collections" });
    const [modificationAgentMode, setModificationAgentMode] = useState<ModificationAgentMode>(loadModificationAgentMode);

    const bottomRef = useRef<HTMLDivElement | null>(null);
    const conversationMenuRef = useRef<HTMLDivElement | null>(null);
    const hasConsumedLaunchRef = useRef(false);
    const syncedConversationScopeRef = useRef<string | null>(null);
    const modificationCloseTimeoutRef = useRef<number | null>(null);
    const deleteConfirmDialogRef = useRef<HTMLDivElement | null>(null);
    const deleteConfirmCancelRef = useRef<HTMLButtonElement | null>(null);
    const conversationDeleteDialogRef = useRef<HTMLDivElement | null>(null);
    const conversationDeleteCancelRef = useRef<HTMLButtonElement | null>(null);
    const previouslyFocusedElementRef = useRef<HTMLElement | null>(null);

    const {
        sidebarWidth,
        modPanelWidth,
        isSidebarOpen,
        isMobile,
        isResizing,
        isSidebarToggling,
        toggleSidebar,
        openSidebar,
        startSidebarResize,
        startModPanelResize,
    } = useResizableLayout({ defaultSidebarOpen: false, restoreSidebarOpen: false }); // Run the useResizableLayout hook to get layout-related state and handlers

    const {
        messages,
        conversations,
        input,
        isQuerying,
        isLoadingConversations,
        isLoadingConversationMessages,
        conversationsError,
        conversationMessagesError,
        conversationId,
        isAgenticSearchEnabled,
        setInput,
        toggleAgenticSearch,
        appendMessage,
        startProgressMessage,
        pushProgressStep,
        finishProgressMessage,
        refreshConversations,
        loadConversationMessages,
        renameConversation,
        deleteConversation,
        startNewConversation,
        handleQuery,
    } =
        useChat(); // Run the useChat hook to get chat-related state and handlers

    const {
        collections,
        isLoadingCollections,
        collectionError,
        activeCollectionId,
        activeCollection,
        setActiveCollectionId,
        refreshCollections,
        createNewCollection,
        renameExistingCollection,
        deleteExistingCollection,
        files,
        isLoadingFiles,
        fileListError,
        fetchFiles,
        deletingFileId,
        openTabs,
        activeTab,
        activeTabData,
        activeTabAsync,
        handleRefreshDocuments,
        deleteFile,
        openDocumentTab,
        openDocumentTabAndEdit,
        closeDocumentTab,
        setActiveDocumentTab,
        loadMoreActiveTab,
        createNewBlankFile,
        renameFile,
        pendingCreationFileIds,
        pendingSaveJobsByFileId,
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
        acceptActiveFileProposals,
        rejectActiveFileProposals,
        clearAgentState,
    } = useDocuments();

    const {
        enqueueFiles: enqueueUploadFiles,
        openModal: openUploadModal,
        subscribeToCompletions: subscribeToUploadCompletions,
        hasActiveUploads,
    } = useUploadQueue();

    useEffect(() => {
        if (hasConsumedLaunchRef.current) return;
        hasConsumedLaunchRef.current = true;

        const launch = consumeConversationLaunch();
        if (!launch) return;

        setChatScope(launch.scope);
        if (launch.scope.type === "collection") {
            // Browsing sources and query scope can diverge later, but launches
            // from a collection should open that collection's files initially.
            setActiveCollectionId(launch.scope.collectionId);
            openSidebar();
        }
        if (launch.prompt?.trim()) {
            setInput(launch.prompt);
            void handleQuery({
                query: launch.prompt,
                searchScope: launch.scope.type,
                collectionId: launch.scope.type === "collection" ? launch.scope.collectionId : null,
                collectionName: launch.scope.type === "collection" ? launch.scope.collectionName ?? null : null,
                seedTopK: 8,
                maxSteps: 10,
            });
        }
    }, [handleQuery, openSidebar, setActiveCollectionId, setInput]);

    useEffect(() => {
        setSelectedFileIds(new Set());
        setHighlightedSelection(null);
        setSelectionError(null);
        setPendingDeleteFile(null);
        setPendingDeleteConversation(null);
    }, [activeCollectionId]);

    useEffect(() => {
        window.localStorage.setItem(MODIFICATION_AGENT_MODE_STORAGE_KEY, modificationAgentMode);
    }, [modificationAgentMode]);

    useEffect(() => {
        return () => {
            if (modificationCloseTimeoutRef.current !== null) {
                window.clearTimeout(modificationCloseTimeoutRef.current);
            }
        };
    }, []);

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
                        modificationAgentMode,
                        onProgress
                    );
                appendMessage({
                    role: "ai",
                        text: result.ok
                            ? result.summary ?? "Review the proposals in the edit panel."
                            : `Edit failed: ${result.error ?? "Unknown error"}`,
                });
                progressStatus = result.ok ? "completed" : "failed";
                if (result.ok) {
                    setIsModificationPanelOpen(true);
                    if (isMobile) {
                        setMobileWorkspace("document");
                    }
                }
                if (result.ok && selectedRange) {
                    setHighlightedSelection(null);
                    setSelectionError(null);
                }
            } finally {
                finishProgressMessage(progressMessageId, progressStatus);
            }
            return;
        }

        await handleQuery({
            searchScope: chatScope.type,
            collectionId: chatScope.type === "collection" ? chatScope.collectionId : null,
            collectionName: chatScope.type === "collection" ? chatScope.collectionName ?? null : null,
        });
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
                `Deleted "${result.data.fileName}" from Documind ` +
                `(${result.data.deletedParentChunks} parent chunks, ${result.data.deletedChildChunks} child chunks). ` +
                `S3 cleanup: ${result.data.s3Status}.${warningText}`,
        });
        setPendingDeleteFile(null);
    }, [appendMessage, deleteFile, pendingDeleteFile]);

    const handleRequestDeleteFile = useCallback((fileId: string) => {
        if (pendingSaveJobsByFileId[fileId]) {
            appendMessage({ role: "ai", text: "That file is still saving. Please wait for the background save to finish before deleting it." });
            return;
        }
        const fileName = files.find((file) => file.fileId === fileId)?.fileName ?? fileId;
        setPendingDeleteFile({ fileId, fileName });
    }, [appendMessage, files, pendingSaveJobsByFileId]);

    const handleCancelDeleteFile = useCallback(() => {
        if (deletingFileId) return;
        setPendingDeleteFile(null);
    }, [deletingFileId]);

    const handleRequestDeleteConversation = useCallback((conversationId: string, title: string) => {
        if (deletingConversationId) return;
        setPendingDeleteConversation({ conversationId, title });
    }, [deletingConversationId]);

    const handleCancelDeleteConversation = useCallback(() => {
        if (deletingConversationId) return;
        setPendingDeleteConversation(null);
    }, [deletingConversationId]);

    const handleConfirmDeleteConversation = useCallback(async () => {
        if (!pendingDeleteConversation || deletingConversationId) return;

        setDeletingConversationId(pendingDeleteConversation.conversationId);
        const success = await deleteConversation(pendingDeleteConversation.conversationId);
        setDeletingConversationId(null);

        if (success) {
            setPendingDeleteConversation(null);
            setRenamingConversationId(null);
            setRenameTitle("");
        }
    }, [deleteConversation, deletingConversationId, pendingDeleteConversation]);

    useEffect(() => {
        if (!pendingDeleteFile) return;

        previouslyFocusedElementRef.current = document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;

        const dialog = deleteConfirmDialogRef.current;
        const initialFocusTarget = deleteConfirmCancelRef.current
            ?? dialog?.querySelector<HTMLElement>("button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])")
            ?? dialog;
        initialFocusTarget?.focus();

        const handleKeyDown = (event: globalThis.KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                if (!deletingFileId) {
                    handleCancelDeleteFile();
                }
                return;
            }
            if (event.key !== "Tab") return;
            const activeDialog = deleteConfirmDialogRef.current;
            if (!activeDialog) return;

            const focusable = Array.from(
                activeDialog.querySelectorAll<HTMLElement>(
                    "button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])"
                )
            ).filter((element) => !element.hasAttribute("disabled"));
            if (focusable.length === 0) {
                event.preventDefault();
                activeDialog.focus();
                return;
            }

            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            const activeElement = document.activeElement as HTMLElement | null;

            if (event.shiftKey) {
                if (activeElement === first || !activeDialog.contains(activeElement)) {
                    event.preventDefault();
                    last.focus();
                }
                return;
            }

            if (activeElement === last || !activeDialog.contains(activeElement)) {
                event.preventDefault();
                first.focus();
            }
        };

        document.addEventListener("keydown", handleKeyDown);
        return () => {
            document.removeEventListener("keydown", handleKeyDown);
            const previouslyFocused = previouslyFocusedElementRef.current;
            if (previouslyFocused && previouslyFocused.isConnected) {
                previouslyFocused.focus();
            }
            previouslyFocusedElementRef.current = null;
        };
    }, [deletingFileId, handleCancelDeleteFile, pendingDeleteFile]);

    useEffect(() => {
        if (!pendingDeleteConversation) return;

        previouslyFocusedElementRef.current = document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;

        const dialog = conversationDeleteDialogRef.current;
        const initialFocusTarget = conversationDeleteCancelRef.current
            ?? dialog?.querySelector<HTMLElement>("button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])")
            ?? dialog;
        initialFocusTarget?.focus();

        const handleKeyDown = (event: globalThis.KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                if (!deletingConversationId) {
                    handleCancelDeleteConversation();
                }
                return;
            }
            if (event.key !== "Tab") return;
            const activeDialog = conversationDeleteDialogRef.current;
            if (!activeDialog) return;

            const focusable = Array.from(
                activeDialog.querySelectorAll<HTMLElement>(
                    "button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])"
                )
            ).filter((element) => !element.hasAttribute("disabled"));
            if (focusable.length === 0) {
                event.preventDefault();
                activeDialog.focus();
                return;
            }

            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            const activeElement = document.activeElement as HTMLElement | null;

            if (event.shiftKey) {
                if (activeElement === first || !activeDialog.contains(activeElement)) {
                    event.preventDefault();
                    last.focus();
                }
                return;
            }

            if (activeElement === last || !activeDialog.contains(activeElement)) {
                event.preventDefault();
                first.focus();
            }
        };

        document.addEventListener("keydown", handleKeyDown);
        return () => {
            document.removeEventListener("keydown", handleKeyDown);
            const previouslyFocused = previouslyFocusedElementRef.current;
            if (previouslyFocused && previouslyFocused.isConnected) {
                previouslyFocused.focus();
            }
            previouslyFocusedElementRef.current = null;
        };
    }, [deletingConversationId, handleCancelDeleteConversation, pendingDeleteConversation]);

    const activeChunkSignature = activeTabData?.chunks
        .map((chunk) => `${chunk.parentId}:${chunk.size}`)
        .join("|") ?? "";
    const hasSelectedDocument = Boolean(activeTab);
    const isDesktopWorkspaceActive = !isMobile && isModificationPanelOpen && hasSelectedDocument;
    const activeMobileWorkspace = isMobile ? mobileWorkspace : "chat";
    const chatEmptyStateMode = isEditMode && !hasSelectedDocument ? "no-document" : "welcome";
    // The active chat title is driven by selected conversation metadata; fallback is a new-chat label.
    const activeConversationSummary = useMemo(
        () => conversations.find((conversation) => conversation.conversationId === conversationId) ?? null,
        [conversations, conversationId]
    );
    const chatScopeLabel = useMemo(() => {
        if (chatScope.type === "all_collections") return "All collections";
        return chatScope.collectionName
            || collections.find((collection) => collection.collectionId === chatScope.collectionId)?.name
            || "Selected collection";
    }, [chatScope, collections]);
    const activeCollectionName = activeCollection?.name
        ?? collections.find((collection) => collection.collectionId === activeCollectionId)?.name
        ?? "Selected collection";
    const uploadTarget = useMemo(
        () => ({
            collectionId: activeCollectionId,
            collectionName: activeCollectionId ? activeCollectionName : null,
        }),
        [activeCollectionId, activeCollectionName]
    );
    const handleOpenUploadPicker = useCallback(() => {
        openUploadModal(uploadTarget);
    }, [openUploadModal, uploadTarget]);
    const handleUploadFiles = useCallback((incomingFiles: FileList | File[]) => {
        enqueueUploadFiles(incomingFiles, uploadTarget);
    }, [enqueueUploadFiles, uploadTarget]);
    const modificationCollectionScope: ChatScope = activeCollectionId
        ? {
            type: "collection",
            collectionId: activeCollectionId,
            collectionName: activeCollectionName,
        }
        : { type: "all_collections" };
    const currentChatTitle = activeConversationSummary?.title?.trim() || "New AI chat";
    const previousConversations = useMemo(
        () => conversations.filter((conversation) => conversation.conversationId !== conversationId),
        [conversations, conversationId]
    );
    const conversationHistoryCount = conversations.length;
    const activeFileName = activeTab
        ? files.find((file) => file.fileId === activeTab)?.fileName ?? activeTab
        : "No file selected";
    const isDeletingActiveFile = Boolean(activeTab && deletingFileId === activeTab);
    const isPendingSaveActiveFile = Boolean(activeTab && pendingSaveJobsByFileId[activeTab]);
    const pendingSaveFileIds = useMemo(
        () => new Set(Object.keys(pendingSaveJobsByFileId)),
        [pendingSaveJobsByFileId]
    );
    const trimmedDesktopFileNameDraft = desktopFileNameDraft.trim();
    const isDesktopFileNameDirty = Boolean(
        activeTab
        && trimmedDesktopFileNameDraft
        && trimmedDesktopFileNameDraft !== activeFileName
    );
    const isDesktopSaveDisabled =
        isSavingActiveDocument
        || isSavingDesktopFileName
        || isPendingSaveActiveFile
        || (!isActiveDocumentDirty && !isDesktopFileNameDirty);

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
    const hasUnresolvedActiveFileSuggestions = Boolean(
        activeTab && pendingModificationItems.some((item) => item.fileId === activeTab && item.pendingCount > 0)
    );

    const handleNavigateToModification = useCallback(async (fileId: string, proposalKey: string) => {
        setIsModificationPanelOpen(true);
        setIsEditMode(true);
        if (isMobile) {
            setMobileWorkspace("document");
        }

        if (activeTab !== fileId) {
            await openDocumentTab(fileId);
        }
        setFocusedProposalKey(proposalKey);
    }, [activeTab, isMobile, openDocumentTab]);

    const handleMobileWorkspaceChange = useCallback((workspace: MobileWorkspace) => {
        if (!isMobile) return;

        setMobileWorkspace(workspace);
        if (workspace === "chat") {
            setIsModificationPanelOpen(false);
            return;
        }

        if (workspace === "files") {
            setIsModificationPanelOpen(false);
            return;
        }

        setIsModificationPanelOpen(true);
    }, [isMobile]);

    const validateDesktopFileNameDraft = useCallback((): string | null => {
        const trimmed = desktopFileNameDraft.trim();
        if (!activeTab) return "No file selected.";
        if (!trimmed) return "File name must not be empty.";
        const duplicate = files.find(
            (file) =>
                file.fileId !== activeTab
                && file.fileName.trim().toLowerCase() === trimmed.toLowerCase()
        );
        if (duplicate) return `A file named "${duplicate.fileName}" already exists.`;
        return null;
    }, [activeTab, desktopFileNameDraft, files]);

    const handleStartEditingActiveDocument = useCallback(() => {
        if (isPendingSaveActiveFile) return;
        setDesktopFileNameDraft(activeFileName);
        setDesktopFileNameError(null);
        startEditingActiveDocument();
    }, [activeFileName, isPendingSaveActiveFile, startEditingActiveDocument]);

    const handleCancelDesktopEditing = useCallback(() => {
        setDesktopFileNameDraft(activeFileName);
        setDesktopFileNameError(null);
        cancelEditingActiveDocument();
    }, [activeFileName, cancelEditingActiveDocument]);

    const handleSaveDesktopEditing = useCallback(async () => {
        if (!activeTab) return;
        const validationError = validateDesktopFileNameDraft();
        if (validationError) {
            setDesktopFileNameError(validationError);
            return;
        }

        if (isActiveDocumentDirty) {
            const didSaveContent = await saveEditingActiveDocument(
                isDesktopFileNameDirty ? { newFileName: trimmedDesktopFileNameDraft } : undefined
            );
            if (!didSaveContent) return;
            setDesktopFileNameError(null);
            return;
        }

        if (isDesktopFileNameDirty) {
            setIsSavingDesktopFileName(true);
            const result = await renameFile(activeTab, trimmedDesktopFileNameDraft);
            setIsSavingDesktopFileName(false);
            if (!result.ok) {
                setDesktopFileNameError(result.error ?? "Failed to rename file.");
                return;
            }
            setDesktopFileNameError(null);
        }

        if (!isActiveDocumentDirty && isDesktopFileNameDirty) {
            cancelEditingActiveDocument();
        }
    }, [
        activeTab,
        cancelEditingActiveDocument,
        isActiveDocumentDirty,
        isDesktopFileNameDirty,
        renameFile,
        saveEditingActiveDocument,
        trimmedDesktopFileNameDraft,
        validateDesktopFileNameDraft,
    ]);

    // Creates a fresh chat and resets menu/rename UI state.
    const handleStartNewConversationFromHeader = useCallback(() => {
        startNewConversation();
        syncedConversationScopeRef.current = null;
        setChatScope({ type: "all_collections" });
        setRenamingConversationId(null);
        setRenameTitle("");
        setDeletingConversationId(null);
        setPendingDeleteConversation(null);
        setIsConversationMenuOpen(false);
    }, [startNewConversation]);

    const handleSelectConversationFromMenu = useCallback((targetConversationId: string) => {
        setIsConversationMenuOpen(false);
        setRenamingConversationId(null);
        setRenameTitle("");
        setDeletingConversationId(null);
        setPendingDeleteConversation(null);
        void loadConversationMessages(targetConversationId);
    }, [loadConversationMessages]);

    const chatControlsDisabled = isQuerying || isAgentGenerating || isEditMode;
    const chatScopeControls = (
        <>
            <ScopePicker
                scope={chatScope}
                collections={collections}
                disabled={chatControlsDisabled}
                isLoadingCollections={isLoadingCollections}
                collectionError={collectionError}
                onScopeChange={setChatScope}
            />
            <div className="search-mode-toggle-group" role="group" aria-label="Search mode">
                <button
                    type="button"
                    className={`search-mode-option ${!isAgenticSearchEnabled ? "active" : ""}`}
                    onClick={() => {
                        if (isAgenticSearchEnabled) toggleAgenticSearch();
                    }}
                    disabled={chatControlsDisabled}
                    aria-pressed={!isAgenticSearchEnabled}
                    title="Use standard retrieve-and-answer search"
                >
                    Standard
                </button>
                <button
                    type="button"
                    className={`search-mode-option ${isAgenticSearchEnabled ? "active" : ""}`}
                    onClick={() => {
                        if (!isAgenticSearchEnabled) toggleAgenticSearch();
                    }}
                    disabled={chatControlsDisabled}
                    aria-pressed={isAgenticSearchEnabled}
                    title="Use agentic multi-step search"
                >
                    Agentic
                </button>
            </div>
        </>
    );
    const modificationCollectionControls = (
        <ScopePicker
            scope={modificationCollectionScope}
            collections={collections}
            disabled={isQuerying || isAgentGenerating || isEditingActiveDocument}
            isLoadingCollections={isLoadingCollections}
            collectionError={collectionError}
            onScopeChange={(scope) => {
                if (scope.type !== "collection") return;
                setActiveCollectionId(scope.collectionId);
            }}
            triggerPrefix="Modify"
            instruction="Select the collection the agent should modify"
            includeAllCollections={false}
        />
    );

    const renderChatWorkspace = (emptyStateMode: "welcome" | "no-document") => (
        <div className="chat-stage-shell">
            {/* Minimal Notion-style chat title bar:
                - Left: collapsible current chat title
                - Right: icon-only new chat action */}
            <section
                className="chat-stage-conversation-switcher"
                aria-label="Conversation history switcher"
                ref={conversationMenuRef}
            >
                <div className="chat-stage-conversation-bar">
                    <button
                        className={`chat-stage-current-chat-btn ${isConversationMenuOpen ? "open" : ""}`}
                        type="button"
                        onClick={() => {
                            setIsConversationMenuOpen((previous) => !previous);
                            if (renamingConversationId) {
                                setRenamingConversationId(null);
                                setRenameTitle("");
                            }
                        }}
                        aria-expanded={isConversationMenuOpen}
                        aria-label="Toggle conversation history menu"
                    >
                        <span className="chat-stage-current-chat-label">{currentChatTitle}</span>
                        <span className="chat-stage-current-chat-chevron" aria-hidden="true">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="6 9 12 15 18 9" />
                            </svg>
                        </span>
                    </button>

                    <button
                        className="chat-stage-new-chat-btn"
                        type="button"
                        onClick={handleStartNewConversationFromHeader}
                        disabled={isQuerying || isLoadingConversationMessages}
                        aria-label="Start a new chat"
                        title="New AI chat"
                    >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                            <path d="M18.375 2.625a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4Z" />
                        </svg>
                    </button>
                </div>

                {isConversationMenuOpen && (
                    <div className="chat-stage-conversation-dropdown" role="menu" aria-label="Conversation history list">
                        <div className="chat-stage-conversation-dropdown-header">
                            <div className="chat-stage-conversation-dropdown-label">Older</div>
                            <div className="chat-stage-conversation-count">{conversationHistoryCount}/20</div>
                        </div>
                        <div className="chat-stage-conversation-limit-tip">
                            Max 20 conversation histories per user. Oldest conversation is deleted once limit is exceeded.
                        </div>

                        {isLoadingConversations ? (
                            <div className="conversation-switcher-status">Loading conversations...</div>
                        ) : conversationsError ? (
                            <div className="conversation-switcher-status error">{conversationsError}</div>
                        ) : previousConversations.length === 0 ? (
                            <div className="conversation-switcher-status">No conversation history yet.</div>
                        ) : (
                            <div className="chat-stage-conversation-dropdown-list">
                                {previousConversations.map((conversation) => (
                                    <div
                                        key={conversation.conversationId}
                                        className={`chat-stage-conversation-row-wrapper ${renamingConversationId === conversation.conversationId ? "renaming" : ""}`}
                                    >
                                        {renamingConversationId === conversation.conversationId ? (
                                            <div className="chat-stage-conversation-row-rename">
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
                                                        {"\u2713"}
                                                    </button>
                                                    <button
                                                        onClick={handleCancelRename}
                                                        disabled={isRenamingConversation}
                                                        className="rename-btn rename-cancel"
                                                        type="button"
                                                        title="Cancel (Esc)"
                                                    >
                                                        {"\u2715"}
                                                    </button>
                                                </div>
                                            </div>
                                        ) : (
                                            <>
                                                <button
                                                    className="chat-stage-conversation-row"
                                                    onClick={() => {
                                                        handleSelectConversationFromMenu(conversation.conversationId);
                                                    }}
                                                    disabled={isLoadingConversationMessages}
                                                    type="button"
                                                    role="menuitem"
                                                >
                                                    {conversation.title?.trim() || "New AI chat"}
                                                </button>
                                                <button
                                                    className="chat-stage-conversation-row-rename-trigger"
                                                    type="button"
                                                    onClick={(event) => {
                                                        event.preventDefault();
                                                        event.stopPropagation();
                                                        handleStartRename(
                                                            conversation.conversationId,
                                                            conversation.title?.trim() || "New AI chat"
                                                        );
                                                    }}
                                                    disabled={isLoadingConversationMessages}
                                                    title="Rename conversation"
                                                    aria-label="Rename conversation"
                                                >
                                                    <svg
                                                        width="13"
                                                        height="13"
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
                                                <button
                                                    className="chat-stage-conversation-row-delete-trigger"
                                                    type="button"
                                                    onClick={(event) => {
                                                        event.preventDefault();
                                                        event.stopPropagation();
                                                        handleRequestDeleteConversation(
                                                            conversation.conversationId,
                                                            conversation.title?.trim() || "New AI chat"
                                                        );
                                                    }}
                                                    disabled={
                                                        isLoadingConversationMessages
                                                        || deletingConversationId === conversation.conversationId
                                                    }
                                                    title="Delete conversation"
                                                    aria-label="Delete conversation"
                                                >
                                                    <svg
                                                        width="13"
                                                        height="13"
                                                        viewBox="0 0 24 24"
                                                        fill="none"
                                                        stroke="currentColor"
                                                        strokeWidth="2"
                                                        strokeLinecap="round"
                                                        strokeLinejoin="round"
                                                        aria-hidden
                                                    >
                                                        <path d="M3 6h18" />
                                                        <path d="M8 6V4h8v2" />
                                                        <path d="M19 6l-1 14H6L5 6" />
                                                        <path d="M10 11v5" />
                                                        <path d="M14 11v5" />
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
                    </div>
                )}

                {!isConversationMenuOpen && conversationMessagesError && (
                    <div className="conversation-switcher-status error">{conversationMessagesError}</div>
                )}
            </section>

            <ChatArea
                messages={messages}
                bottomRef={bottomRef}
                emptyStateMode={emptyStateMode}
            />

            <ChatInput
                input={input}
                isQuerying={isQuerying || isAgentGenerating}
                scopeControls={isEditMode ? modificationCollectionControls : chatScopeControls}
                searchScopeLabel={chatScopeLabel}
                isModificationPanelOpen={isModificationPanelOpen}
                isEditMode={isEditMode}
                modificationAgentMode={modificationAgentMode}
                highlightedSelection={highlightedSelection}
                pendingModificationItems={pendingModificationItems}
                onModificationAgentModeChange={setModificationAgentMode}
                onInputChange={setInput}
                onInputKeyDown={handleComposerKeyDown}
                onToggleModificationPanel={handleToggleModificationPanel}
                onClearHighlightedSelection={clearHighlightedSelection}
                onNavigateToModification={(fileId, proposalKey) => { void handleNavigateToModification(fileId, proposalKey); }}
                onSend={() => { void handleComposerSend(); }}
            />
        </div>
    );

    useEffect(() => subscribeToUploadCompletions((event) => {
        if (!event.ok) return;

        void refreshCollections(activeCollectionId, { force: true });
        const uploadedToVisibleCollection =
            !event.item.collectionId || event.item.collectionId === activeCollectionId;
        if (uploadedToVisibleCollection) {
            void fetchFiles();
        }
    }), [
        activeCollectionId,
        fetchFiles,
        refreshCollections,
        subscribeToUploadCompletions,
    ]);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isQuerying]);

    useEffect(() => {
        void refreshConversations();
    }, [refreshConversations]);

    useEffect(() => {
        if (!isConversationMenuOpen) return;

        const handleDocumentPointerDown = (event: MouseEvent) => {
            const targetNode = event.target as Node | null;
            if (!targetNode || !conversationMenuRef.current) return;
            if (!conversationMenuRef.current.contains(targetNode)) {
                setIsConversationMenuOpen(false);
                setRenamingConversationId(null);
                setRenameTitle("");
            }
        };

        const handleDocumentEscape = (event: globalThis.KeyboardEvent) => {
            if (event.key !== "Escape") return;
            setIsConversationMenuOpen(false);
            setRenamingConversationId(null);
            setRenameTitle("");
        };

        document.addEventListener("mousedown", handleDocumentPointerDown);
        document.addEventListener("keydown", handleDocumentEscape);
        return () => {
            document.removeEventListener("mousedown", handleDocumentPointerDown);
            document.removeEventListener("keydown", handleDocumentEscape);
        };
    }, [isConversationMenuOpen]);

    useEffect(() => {
        if (!isEditMode || isEditingActiveDocument) {
            setHighlightedSelection(null);
            setSelectionError(null);
        }
    }, [isEditMode, isEditingActiveDocument]);

    useEffect(() => {
        if (!conversationId || syncedConversationScopeRef.current === conversationId || messages.length === 0) {
            return;
        }
        const latestScopedMessage = [...messages]
            .reverse()
            .find((message) => message.kind === "text" && message.searchScope);
        if (!latestScopedMessage || latestScopedMessage.kind !== "text") {
            syncedConversationScopeRef.current = conversationId;
            return;
        }

        if (latestScopedMessage.searchScope === "collection" && latestScopedMessage.collectionId) {
            setChatScope({
                type: "collection",
                collectionId: latestScopedMessage.collectionId,
                collectionName: latestScopedMessage.collectionName ?? undefined,
            });
            syncedConversationScopeRef.current = conversationId;
            return;
        }

        if (latestScopedMessage.searchScope === "all_collections") {
            setChatScope({ type: "all_collections" });
        }
        syncedConversationScopeRef.current = conversationId;
    }, [conversationId, messages]);

    useEffect(() => {
        setHighlightedSelection(null);
        setSelectionError(null);
    }, [activeTab]);

    useEffect(() => {
        setDesktopFileNameDraft(activeFileName);
        setDesktopFileNameError(null);
    }, [activeFileName, activeTab]);

    useEffect(() => {
        setHighlightedSelection(null);
        setSelectionError(null);
    }, [activeChunkSignature]);

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
        if (modificationCloseTimeoutRef.current !== null) {
            window.clearTimeout(modificationCloseTimeoutRef.current);
            modificationCloseTimeoutRef.current = null;
        }
        setIsModificationPanelClosing(false);

        if (isMobile) {
            if (!isModificationPanelOpen || mobileWorkspace !== "document") {
                setIsModificationPanelOpen(true);
                setMobileWorkspace("document");
                setIsEditMode(true);
                return;
            }

            if (!isEditMode) {
                setIsEditMode(true);
                return;
            }

            setIsEditMode(false);
            setSelectedFileIds(new Set());
            clearHighlightedSelection();
            setFocusedProposalKey(null);
            return;
        }

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
        if (!isMobile && isModificationPanelOpen) {
            if (modificationCloseTimeoutRef.current !== null) {
                window.clearTimeout(modificationCloseTimeoutRef.current);
            }
            setIsModificationPanelClosing(true);
            setIsEditMode(false);
            setSelectedFileIds(new Set());
            clearHighlightedSelection();
            setFocusedProposalKey(null);
            modificationCloseTimeoutRef.current = window.setTimeout(() => {
                setIsModificationPanelOpen(false);
                setIsModificationPanelClosing(false);
                modificationCloseTimeoutRef.current = null;
            }, 220);
            return;
        }

        setIsModificationPanelOpen(false);
        if (isMobile) {
            setMobileWorkspace("chat");
        }
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
            isSaving={isSavingActiveDocument || isPendingSaveActiveFile}
            isDirty={isActiveDocumentDirty}
            saveError={saveError}
            isEditMode={isEditMode}
            selectedFileIds={selectedFileIds}
            activeCollectionName={activeCollectionName}
            modificationAgentMode={modificationAgentMode}
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
            onSaveEditing={() => {
                if (!isActiveDocumentDirty) return;
                void saveEditingActiveDocument();
            }}
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
            onAcceptActiveFileProposals={acceptActiveFileProposals}
            onRejectActiveFileProposals={rejectActiveFileProposals}
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
            isSaving={isSavingActiveDocument || isPendingSaveActiveFile}
            isDirty={isActiveDocumentDirty}
            saveError={saveError}
            isEditMode={isEditMode}
            selectedFileIds={selectedFileIds}
            activeCollectionName={activeCollectionName}
            modificationAgentMode={modificationAgentMode}
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
            onSaveEditing={() => {
                if (!isActiveDocumentDirty) return;
                void saveEditingActiveDocument();
            }}
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
            onAcceptActiveFileProposals={acceptActiveFileProposals}
            onRejectActiveFileProposals={rejectActiveFileProposals}
            onClearAgentProposals={clearAgentState}
            focusedProposalKey={focusedProposalKey}
            onFocusedProposalHandled={() => setFocusedProposalKey(null)}
        />
    );

    return (
        <div className="mainpage-shell">
            <GlobalSidebar mode="conversation" />
            <div
                className={`app-root ${isMobile ? "mobile-layout" : ""} mobile-workspace-${activeMobileWorkspace} ${isSidebarOpen ? "sidebar-open" : "sidebar-closed"} ${isModificationPanelOpen ? "mod-panel-open" : ""} ${isResizing ? "is-resizing" : ""} ${isSidebarToggling ? "is-sidebar-toggling" : ""} ${pendingDeleteFile || pendingDeleteConversation ? "delete-modal-open" : ""}`}
                style={{
                    "--sidebar-width": `${sidebarWidth}px`,
                    "--mod-panel-width": `${modPanelWidth}px`,
                    "--assistant-stage-width": `${modPanelWidth}px`,
                } as CSSProperties}
            >
            {!isMobile && !isSidebarOpen && (
                <button
                    className="workspace-sidebar-expand-tab"
                    onClick={toggleSidebar}
                    aria-label="Show sources"
                    title="Show sources"
                    type="button"
                >
                                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                            <polyline points="9 18 15 12 9 6" />
                                        </svg>
                                    </button>
                                )}
            <div className={`sidebar-container ${isMobile ? activeMobileWorkspace === "files" ? "open" : "closed" : isSidebarOpen ? "open" : "closed"}`}>
                <Sidebar
                    collections={collections}
                    activeCollectionId={activeCollectionId}
                    isLoadingCollections={isLoadingCollections}
                    collectionError={collectionError}
                    files={files}
                    isLoadingFiles={isLoadingFiles}
                    fileListError={fileListError}
                    activeTab={activeTab}
                    isEditMode={isEditMode}
                    selectedFileIds={selectedFileIds}
                    onToggleFileSelection={handleToggleFileSelection}
                    onCollapseSources={!isMobile ? toggleSidebar : undefined}
                    onOpenUploadPicker={handleOpenUploadPicker}
                    onUploadFiles={handleUploadFiles}
                    isUploadQueueActive={hasActiveUploads}
                    onSelectCollection={(collectionId) => setActiveCollectionId(collectionId)}
                    onCreateCollection={async (name) => {
                        const result = await createNewCollection(name);
                        if (!result.ok) {
                            appendMessage({ role: "ai", text: result.error ?? "Failed to create collection." });
                        } else {
                            appendMessage({ role: "ai", text: `Created collection "${name.trim()}".` });
                        }
                        return result;
                    }}
                    onRenameCollection={async (collectionId, newName) => {
                        const oldName = collections.find((entry) => entry.collectionId === collectionId)?.name ?? "collection";
                        const result = await renameExistingCollection(collectionId, newName);
                        if (!result.ok) {
                            appendMessage({ role: "ai", text: result.error ?? "Failed to rename collection." });
                        } else {
                            appendMessage({ role: "ai", text: `Renamed collection "${oldName}" to "${newName}".` });
                        }
                        return result;
                    }}
                    onDeleteCollection={async (collectionId) => {
                        const oldName = collections.find((entry) => entry.collectionId === collectionId)?.name ?? "collection";
                        const result = await deleteExistingCollection(collectionId);
                        if (!result.ok || !result.data) {
                            appendMessage({ role: "ai", text: result.error ?? "Failed to delete collection." });
                            return result;
                        }

                        appendMessage({
                            role: "ai",
                            text:
                                `Deleted collection "${oldName}" and ${result.data.deletedFiles} file(s) ` +
                                `(${result.data.deletedParentChunks} parent chunks, ${result.data.deletedChildChunks} child chunks).` +
                                `${result.warningText ?? ""}`,
                        });
                        return result;
                    }}
                    onOpenFile={(fileId) => {
                        void openDocumentTab(fileId);
                        setIsModificationPanelOpen(true);
                        if (isMobile) {
                            setMobileWorkspace("document");
                        }
                    }}
                    onRefreshFiles={() => { void handleRefreshDocuments(); }}
                    onCreateBlankFile={async (fileName) => {
                        // createNewBlankFile returns immediately (optimistic) with a tempId.
                        // The DB write happens in the background — no await needed here.
                        const result = await createNewBlankFile(fileName);
                        if (result.ok && result.fileId) {
                            setIsModificationPanelOpen(true);
                            if (isMobile) {
                                setMobileWorkspace("document");
                            }
                            // initialContent is the placeholder; skip the DB load entirely.
                            await openDocumentTabAndEdit(result.fileId, result.initialContent);
                        }
                        return result;
                    }}
                    onRenameFile={async (fileId, newName) => {
                        const result = await renameFile(fileId, newName);
                        if (!result.ok) {
                            appendMessage({ role: "ai", text: result.error ?? `Failed to rename file.` });
                        }
                        return result;
                    }}
                    onRequestDeleteFile={handleRequestDeleteFile}
                    pendingCreationFileIds={pendingCreationFileIds}
                    pendingSaveFileIds={pendingSaveFileIds}
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
                {isMobile ? (
                    renderChatWorkspace(chatEmptyStateMode)
                ) : isDesktopWorkspaceActive ? (
                    <div className={`desktop-edit-workspace ${isModificationPanelClosing ? "closing" : ""}`} aria-live="polite">
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
                                    {isEditingActiveDocument ? (
                                        <label className="desktop-stage-file-name-editor">
                                            <span className="desktop-stage-file-name-label">File name</span>
                                            <input
                                                className="desktop-stage-file-name-input"
                                                type="text"
                                                value={desktopFileNameDraft}
                                                onChange={(event) => {
                                                    setDesktopFileNameDraft(event.target.value);
                                                    if (desktopFileNameError) {
                                                        setDesktopFileNameError(null);
                                                    }
                                                }}
                                                disabled={isSavingActiveDocument || isSavingDesktopFileName}
                                                aria-invalid={desktopFileNameError ? "true" : "false"}
                                            />
                                            {desktopFileNameError && (
                                                <span className="desktop-stage-file-name-error">{desktopFileNameError}</span>
                                            )}
                                        </label>
                                    ) : (
                                        <h3 className="desktop-stage-file-name">{activeFileName}</h3>
                                    )}
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
                                                    onClick={() => { void handleSaveDesktopEditing(); }}
                                                    disabled={isDesktopSaveDisabled}
                                                >
                                                    {isSavingActiveDocument || isSavingDesktopFileName ? "Saving..." : "Save"}
                                                </button>
                                                <button
                                                    className="cancel-btn"
                                                    type="button"
                                                    onClick={handleCancelDesktopEditing}
                                                    disabled={isSavingActiveDocument || isSavingDesktopFileName}
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
                                                onClick={handleStartEditingActiveDocument}
                                                disabled={isSavingActiveDocument || isPendingSaveActiveFile || isDeletingActiveFile || Boolean(activeTabAsync?.isLoading) || hasUnresolvedActiveFileSuggestions}
                                            >
                                                Edit
                                            </button>
                                            <button
                                                className="delete-btn"
                                                type="button"
                                                onClick={() => { if (activeTab) handleRequestDeleteFile(activeTab); }}
                                                disabled={isSavingActiveDocument || isPendingSaveActiveFile || isDeletingActiveFile || Boolean(activeTabAsync?.isLoading)}
                                            >
                                                {isDeletingActiveFile ? "Deleting..." : "Delete"}
                                            </button>
                                        </div>
                                    )}

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
                <div className={`mod-panel-container ${activeMobileWorkspace === "document" && isModificationPanelOpen ? "open" : "closed"}`}>
                    {modificationPanel}
                </div>
            )}

            {isMobile && (
                <nav className="mobile-workspace-switcher" aria-label="Workspace sections">
                    <button
                        className={`mobile-workspace-button ${activeMobileWorkspace === "files" ? "active" : ""}`}
                        type="button"
                        onClick={() => handleMobileWorkspaceChange("files")}
                        aria-current={activeMobileWorkspace === "files" ? "page" : undefined}
                    >
                        Files
                    </button>
                    <button
                        className={`mobile-workspace-button ${activeMobileWorkspace === "chat" ? "active" : ""}`}
                        type="button"
                        onClick={() => handleMobileWorkspaceChange("chat")}
                        aria-current={activeMobileWorkspace === "chat" ? "page" : undefined}
                    >
                        Chat
                    </button>
                    <button
                        className={`mobile-workspace-button ${activeMobileWorkspace === "document" ? "active" : ""}`}
                        type="button"
                        onClick={() => handleMobileWorkspaceChange("document")}
                        aria-current={activeMobileWorkspace === "document" ? "page" : undefined}
                    >
                        Document
                    </button>
                </nav>
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
                        ref={deleteConfirmDialogRef}
                        tabIndex={-1}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="delete-confirm-title"
                    >
                        <div className="delete-confirm-eyebrow">Delete file</div>
                        <h3 id="delete-confirm-title" className="delete-confirm-title">
                            Remove "{pendingDeleteFile.fileName}"?
                        </h3>
                        <p className="delete-confirm-text">
                            This removes the file from Documind.
                        </p>
                        <div className="delete-confirm-actions">
                            <button
                                className="delete-confirm-cancel"
                                ref={deleteConfirmCancelRef}
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
            {pendingDeleteConversation && (
                <div
                    className="delete-confirm-overlay"
                    onClick={handleCancelDeleteConversation}
                    role="presentation"
                >
                    <div
                        className="delete-confirm-dialog"
                        onClick={(event) => event.stopPropagation()}
                        ref={conversationDeleteDialogRef}
                        tabIndex={-1}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="conversation-delete-confirm-title"
                    >
                        <div className="delete-confirm-eyebrow">Delete conversation</div>
                        <h3 id="conversation-delete-confirm-title" className="delete-confirm-title">
                            Delete this conversation?
                        </h3>
                        <p className="delete-confirm-text">
                            <span className="delete-confirm-target">{pendingDeleteConversation.title}</span>
                            {" "}will be removed from your conversation history.
                        </p>
                        <div className="delete-confirm-actions">
                            <button
                                className="delete-confirm-cancel"
                                ref={conversationDeleteCancelRef}
                                type="button"
                                onClick={handleCancelDeleteConversation}
                                disabled={Boolean(deletingConversationId)}
                            >
                                Keep chat
                            </button>
                            <button
                                className="delete-confirm-submit"
                                type="button"
                                onClick={() => { void handleConfirmDeleteConversation(); }}
                                disabled={Boolean(deletingConversationId)}
                            >
                                {deletingConversationId ? "Deleting..." : "Delete"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
            </div>
        </div>
    );
}



