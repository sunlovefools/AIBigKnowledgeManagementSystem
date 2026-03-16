import { useEffect, useMemo, useRef } from "react";
import MarkdownEditor from "./FileViewingAndModification";
import type {
    AgentProposal,
    FileTabAsyncState,
    FileTabState,
    HighlightedSelection,
    SidebarFileSummary,
} from "../types";

type ModificationPanelProps = {
    files: SidebarFileSummary[];
    activeTab: string | null;
    activeTabData: FileTabState | null;
    activeTabAsync: FileTabAsyncState | null;
    openTabs: string[];
    isLoadingFiles: boolean;
    deletingFileId: string | null;
    editingContent: string;
    isEditing: boolean;
    isSaving: boolean;
    isDirty: boolean;
    saveError: string | null;
    isEditMode: boolean;
    selectedFileIds: Set<string>;
    highlightedSelection: HighlightedSelection | null;
    selectionError: string | null;
    isAgentGenerating: boolean;
    agentProposals: AgentProposal[];
    agentAcceptedMap: Map<string, AgentProposal>;
    agentSavedIds: Set<string>;
    agentRejectedIds: Set<string>;
    agentSavingIds: Set<string>;
    agentError: string | null;
    agentIntention: string | null;
    hideTabs?: boolean;
    hideHeader?: boolean;
    hideDocumentToolbar?: boolean;
    onRefreshDocuments: () => void;
    onClose: () => void;
    onTabSelect: (fileId: string) => void;
    onTabClose: (fileId: string) => void;
    onLoadMoreActiveTab: () => void;
    onStartEditing: () => void;
    onDeleteActiveFile: () => void;
    onEditingContentChange: (nextContent: string) => void;
    onCancelEditing: () => void;
    onSaveEditing: () => void;
    onHighlightedSelectionChange: (selection: HighlightedSelection | null) => void;
    onSelectionErrorChange: (message: string | null) => void;
    onAcceptAgentProposal: (proposal: AgentProposal) => Promise<void>;
    onSaveAgentProposal: (proposal: AgentProposal) => void;
    onRejectAgentProposal: (parentId: string) => void;
    onClearAgentProposals: () => void;
};

function getContainerElement(node: Node): HTMLElement | null {
    if (node instanceof HTMLElement) return node;
    return node.parentElement;
}

type PageChunkSegment = {
    parentId: string;
    content: string;
};

type PageGroup = {
    pageNumber: number;
    segments: PageChunkSegment[];
};

function normalizePageNumbers(pageNumbers: number[] | undefined): number[] {
    if (!pageNumbers || pageNumbers.length === 0) return [0];
    const deduped = Array.from(new Set(pageNumbers.filter((value) => Number.isFinite(value))));
    if (!deduped.length) return [0];
    return deduped.sort((a, b) => a - b);
}

export default function ModificationPanel({
    files,
    activeTab,
    activeTabData,
    activeTabAsync,
    openTabs,
    isLoadingFiles,
    deletingFileId,
    editingContent,
    isEditing,
    isSaving,
    isDirty,
    saveError,
    isEditMode,
    selectedFileIds,
    highlightedSelection,
    selectionError,
    isAgentGenerating,
    agentProposals,
    agentAcceptedMap,
    agentSavedIds,
    agentRejectedIds,
    agentSavingIds,
    agentError,
    agentIntention,
    hideTabs = false,
    hideHeader = false,
    hideDocumentToolbar = false,
    onRefreshDocuments,
    onClose,
    onTabSelect,
    onTabClose,
    onLoadMoreActiveTab,
    onStartEditing,
    onDeleteActiveFile,
    onEditingContentChange,
    onCancelEditing,
    onSaveEditing,
    onHighlightedSelectionChange,
    onSelectionErrorChange,
    onAcceptAgentProposal,
    onSaveAgentProposal,
    onRejectAgentProposal,
    onClearAgentProposals,
}: ModificationPanelProps) {
    const contentRef = useRef<HTMLDivElement | null>(null);
    const previousProposalCountRef = useRef(0);

    const isDeletingActiveFile = Boolean(activeTab && deletingFileId === activeTab);

    useEffect(() => {
        const previousCount = previousProposalCountRef.current;
        if (agentProposals.length > 0 && agentProposals.length !== previousCount) {
            contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
        }
        previousProposalCountRef.current = agentProposals.length;
    }, [agentProposals.length]);

    const handleContentScroll = () => {
        if (!contentRef.current || !activeTabData || activeTabAsync?.isLoading || !activeTabData.hasMore) return;
        const { scrollTop, scrollHeight, clientHeight } = contentRef.current;
        if (scrollHeight - scrollTop - clientHeight < 120) void onLoadMoreActiveTab();
    };

    const handleDocumentSelection = () => {
        if (!isEditMode || isEditing || !activeTab || !activeTabData?.chunks.length) return;

        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || selection.isCollapsed || !selection.toString().trim()) {
            onHighlightedSelectionChange(null);
            onSelectionErrorChange(null);
            return;
        }

        const range = selection.getRangeAt(0);
        const startElement = getContainerElement(range.startContainer);
        const endElement = getContainerElement(range.endContainer);
        const startChunk = startElement?.closest<HTMLElement>("[data-parent-id]");
        const endChunk = endElement?.closest<HTMLElement>("[data-parent-id]");

        if (!startChunk || !endChunk) {
            onHighlightedSelectionChange(null);
            onSelectionErrorChange(null);
            return;
        }

        if (startChunk.dataset.parentId !== endChunk.dataset.parentId) {
            onSelectionErrorChange("Highlight text within a single chunk only.");
            onHighlightedSelectionChange(null);
            selection.removeAllRanges();
            return;
        }

        const parentId = startChunk.dataset.parentId ?? "";
        const textRoot = startChunk.querySelector<HTMLElement>(".mod-panel-document-text");
        if (
            !parentId ||
            !textRoot ||
            !textRoot.contains(range.startContainer) ||
            !textRoot.contains(range.endContainer)
        ) {
            onSelectionErrorChange("Highlight text inside the chunk body only.");
            onHighlightedSelectionChange(null);
            selection.removeAllRanges();
            return;
        }

        const prefixRange = range.cloneRange();
        prefixRange.selectNodeContents(textRoot);
        prefixRange.setEnd(range.startContainer, range.startOffset);

        const selectedText = range.toString();
        const startOffset = prefixRange.toString().length;
        const endOffset = startOffset + selectedText.length;
        const chunk = activeTabData.chunks.find((item) => item.parentId === parentId);

        if (!chunk || chunk.content.slice(startOffset, endOffset) !== selectedText) {
            onSelectionErrorChange("The current selection does not match the stored chunk content.");
            onHighlightedSelectionChange(null);
            selection.removeAllRanges();
            return;
        }

        const fileName = files.find((file) => file.fileId === activeTab)?.fileName ?? activeTab;
        onSelectionErrorChange(null);
        onHighlightedSelectionChange({
            fileId: activeTab,
            fileName,
            parentId,
            selectedText,
            startOffset,
            endOffset,
        });
    };

    const editScopeLabel = selectedFileIds.size > 0
        ? `${selectedFileIds.size} file(s) selected`
        : "All files";
    const activeFileName = activeTab
        ? files.find((file) => file.fileId === activeTab)?.fileName ?? activeTab
        : "No file selected";

    const pageGroups = useMemo<PageGroup[]>(() => {
        if (!activeTabData?.chunks.length) return [];

        const grouped = new Map<number, PageGroup>();
        activeTabData.chunks.forEach((chunk) => {
            const pages = normalizePageNumbers(chunk.pageNumbers);
            pages.forEach((pageNumber) => {
                if (!grouped.has(pageNumber)) {
                    grouped.set(pageNumber, { pageNumber, segments: [] });
                }
                grouped.get(pageNumber)?.segments.push({
                    parentId: chunk.parentId,
                    content: chunk.content,
                });
            });
        });

        return Array.from(grouped.values()).sort((a, b) => a.pageNumber - b.pageNumber);
    }, [activeTabData]);

    const showAgentSection = isEditMode && (
        isAgentGenerating ||
        agentProposals.length > 0 ||
        agentError !== null ||
        !activeTab
    );

    return (
        <aside className="modification-panel">
            {!hideTabs && (
                <div className="mod-panel-tabs" role="tablist" aria-label="Opened documents">
                    {openTabs.length === 0 ? (
                        <div className="mod-panel-tabs-empty">Open a file from the sidebar to view full content.</div>
                    ) : (
                        openTabs.map((fileId) => {
                            const fileName = files.find((f) => f.fileId === fileId)?.fileName ?? fileId;
                            return (
                                <div key={fileId} className={`mod-panel-tab ${activeTab === fileId ? "active" : ""}`}>
                                    <button className="mod-panel-tab-label" onClick={() => void onTabSelect(fileId)} type="button">
                                        {fileName}
                                    </button>
                                    <button className="mod-panel-tab-close" onClick={() => onTabClose(fileId)} aria-label={`Close ${fileName}`} type="button">
                                        x
                                    </button>
                                </div>
                            );
                        })
                    )}
                </div>
            )}

            {!hideHeader && (
                <div className="mod-panel-header">
                    <div className="mod-panel-header-title">
                        <h3>{activeFileName}</h3>
                        {isEditMode && (
                            <span className="mod-panel-edit-mode-badge">
                                Edit - {editScopeLabel}
                            </span>
                        )}
                    </div>
                    <div className="mod-panel-header-actions">
                        <button
                            className="mod-panel-refresh-btn"
                            onClick={onRefreshDocuments}
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
                        <button className="mod-panel-close-btn" onClick={onClose} aria-label="Close modifications panel">
                            x
                        </button>
                    </div>
                </div>
            )}

            <div className="mod-panel-content" ref={contentRef} onScroll={handleContentScroll}>
                {showAgentSection && (
                    <section className="mod-panel-agent-section">
                        <div className="preview-header">
                            <h4>
                                AI Proposals
                                {agentIntention && (
                                    <span className="agent-intention-badge">{agentIntention}</span>
                                )}
                            </h4>
                            {agentProposals.length > 0 && (
                                <button className="cancel-btn" type="button" onClick={onClearAgentProposals}>
                                    Clear all
                                </button>
                            )}
                        </div>

                        {isAgentGenerating && (
                            <div className="mod-panel-loading">Agent is searching and generating proposals...</div>
                        )}

                        {agentError && (
                            <div className="mod-panel-save-error">{agentError}</div>
                        )}

                        {!isAgentGenerating && agentProposals.length === 0 && !agentError && (
                            <div className="mod-panel-empty">
                                Type an instruction in the chat to modify documents.
                                <br />
                                <em>
                                    {selectedFileIds.size > 0
                                        ? `Will search ${selectedFileIds.size} selected file(s).`
                                        : "Will search all files - or check files in sidebar to narrow scope."}
                                </em>
                            </div>
                        )}

                        {agentProposals.map((proposal) => {
                            const isAccepted = agentAcceptedMap.has(proposal.parentId);
                            const isSaved = agentSavedIds.has(proposal.parentId);
                            const isRejected = agentRejectedIds.has(proposal.parentId);
                            const isSavingProposal = agentSavingIds.has(proposal.parentId);
                            const isPending = !isAccepted && !isRejected;

                            return (
                                <div
                                    key={`${proposal.parentId}-${proposal.selectionStart ?? "full"}`}
                                    className={`agent-proposal-card ${isAccepted && !isSaved ? "previewing" : ""} ${isSaved ? "saved" : ""} ${isRejected ? "rejected" : ""}`}
                                >
                                    <div className="agent-proposal-header">
                                        <span className="agent-proposal-filename">{proposal.fileName}</span>
                                        <div className="agent-proposal-meta">
                                            {proposal.source === "selection" && (
                                                <span className="agent-proposal-source">Selection</span>
                                            )}
                                            {isSaved && <span className="agent-proposal-status saved">Saved to database</span>}
                                            {isAccepted && !isSaved && <span className="agent-proposal-status previewing">Previewing</span>}
                                            {isRejected && <span className="agent-proposal-status rejected">Rejected</span>}
                                        </div>
                                    </div>

                                    {!isAccepted && !isRejected && (
                                        <div className="agent-diff-blocks">
                                            <div className="agent-diff-block del-block">
                                                <div className="agent-diff-block-label">Before</div>
                                                <pre className="agent-diff-text">{proposal.original}</pre>
                                            </div>
                                            <div className="agent-diff-block add-block">
                                                <div className="agent-diff-block-label">After</div>
                                                <pre className="agent-diff-text">{proposal.proposed}</pre>
                                            </div>
                                        </div>
                                    )}

                                    {isAccepted && !isSaved && (
                                        <div className="agent-preview-hint">
                                            Changes applied locally. Review the full document in the tab above, then save when ready.
                                        </div>
                                    )}

                                    {isPending && (
                                        <div className="ai-preview-actions">
                                            <button
                                                className="save-btn"
                                                type="button"
                                                onClick={() => { void onAcceptAgentProposal(proposal); }}
                                            >
                                                Accept
                                            </button>
                                            <button
                                                className="cancel-btn"
                                                type="button"
                                                onClick={() => onRejectAgentProposal(proposal.parentId)}
                                            >
                                                Reject
                                            </button>
                                        </div>
                                    )}

                                    {isAccepted && !isSaved && (
                                        <div className="ai-preview-actions">
                                            <button
                                                className="save-btn"
                                                type="button"
                                                onClick={() => onSaveAgentProposal(proposal)}
                                                disabled={isSavingProposal}
                                            >
                                                {isSavingProposal ? "Saving..." : "Save to database"}
                                            </button>
                                            <button
                                                className="cancel-btn"
                                                type="button"
                                                onClick={() => onRejectAgentProposal(proposal.parentId)}
                                                disabled={isSavingProposal}
                                            >
                                                Discard
                                            </button>
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </section>
                )}

                {!activeTab ? (
                    !isEditMode && <div className="mod-panel-empty">No file tab selected.</div>
                ) : activeTabAsync?.error ? (
                    <div className="mod-panel-empty">{activeTabAsync.error}</div>
                ) : pageGroups.length ? (
                    <>
                        <section className={`mod-panel-document-window ${isEditing ? "editing" : ""}`}>
                            {!hideDocumentToolbar && (
                                <div className="mod-panel-document-toolbar">
                                    {isEditing ? (
                                        <>
                                            <span className="mod-panel-editing-indicator">Editing mode</span>
                                            <div className="document-action-group">
                                                <button
                                                    className="save-btn"
                                                    type="button"
                                                    onClick={onSaveEditing}
                                                    disabled={isSaving || !isDirty}
                                                >
                                                    {isSaving ? "Saving..." : "Save"}
                                                </button>
                                                <button
                                                    className="cancel-btn"
                                                    type="button"
                                                    onClick={onCancelEditing}
                                                    disabled={isSaving}
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
                                                onClick={onStartEditing}
                                                disabled={isSaving || isDeletingActiveFile || Boolean(activeTabAsync?.isLoading)}
                                            >
                                                Edit
                                            </button>
                                            <button
                                                className="delete-btn"
                                                type="button"
                                                onClick={onDeleteActiveFile}
                                                disabled={isSaving || isDeletingActiveFile || Boolean(activeTabAsync?.isLoading)}
                                            >
                                                {isDeletingActiveFile ? "Deleting..." : "Delete"}
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )}

                            {isEditMode && !isEditing && (
                                <div className="mod-panel-selection-hint">
                                    Highlight text within a single page block to edit directly.
                                </div>
                            )}

                            {selectionError && (
                                <div className="mod-panel-selection-error">{selectionError}</div>
                            )}

                            {isEditing ? (
                                <>
                                    <MarkdownEditor
                                        markdown={editingContent}
                                        editable={!isSaving}
                                        onChange={onEditingContentChange}
                                        className="mod-panel-active-editor"
                                    />
                                </>
                            ) : (
                                <div
                                    className="mod-panel-document-pages"
                                    onMouseUp={handleDocumentSelection}
                                    onKeyUp={handleDocumentSelection}
                                >
                                    {pageGroups.map((group) => (
                                        <section
                                            key={`page-${group.pageNumber}`}
                                            className="mod-panel-document-page"
                                        >
                                            <div className="mod-panel-document-page-content">
                                                {group.segments.map((segment, index) => (
                                                    <article
                                                        key={`${group.pageNumber}-${segment.parentId}-${index}`}
                                                        className={`mod-panel-document-segment ${
                                                            highlightedSelection?.parentId === segment.parentId ? "selected" : ""
                                                        }`}
                                                        data-parent-id={segment.parentId}
                                                    >
                                                            <div className="mod-panel-document-text">
                                                                <MarkdownEditor
                                                                    markdown={segment.content}
                                                                    editable={false}
                                                                    className="mod-panel-segment-editor"
                                                                />
                                                            </div>
                                                    </article>
                                                ))}
                                            </div>
                                            <div className="mod-panel-document-page-label">
                                                Page {group.pageNumber + 1}
                                            </div>
                                        </section>
                                    ))}
                                </div>
                            )}

                            {saveError && <div className="mod-panel-save-error">{saveError}</div>}
                        </section>

                        {activeTabAsync?.isLoading && (
                            <div className="mod-panel-loading">Loading more chunks...</div>
                        )}
                        {activeTabData && !activeTabData.hasMore && (
                            <div className="mod-panel-end">End of document</div>
                        )}
                    </>
                ) : activeTabAsync?.isLoading ? (
                    <div className="mod-panel-loading">Loading full content...</div>
                ) : (
                    <div className="mod-panel-empty">No content available for this file.</div>
                )}
            </div>
        </aside>
    );
}
