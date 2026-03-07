import { useRef } from "react";
import type { AgentProposal, FileTabState, SidebarFileSummary } from "../types";

type ModificationPanelProps = {
    files: SidebarFileSummary[];   // needed to resolve fileId → fileName for tab labels
    activeTab: string | null;      // fileId
    activeTabState: FileTabState | null;
    openTabs: string[];            // fileIds
    isLoadingFiles: boolean;
    editingContent: string;
    isEditing: boolean;
    isSaving: boolean;
    isDirty: boolean;
    saveError: string | null;
    isEditMode: boolean;
    selectedFileIds: Set<string>;
    isAgentGenerating: boolean;
    agentProposals: AgentProposal[];
    agentAcceptedMap: Map<string, AgentProposal>;
    agentSavedIds: Set<string>;
    agentRejectedIds: Set<string>;
    agentSavingIds: Set<string>;
    agentError: string | null;
    agentIntention: string | null;
    onRefreshDocuments: () => void;
    onClose: () => void;
    onTabSelect: (fileId: string) => void;
    onTabClose: (fileId: string) => void;
    onLoadMoreActiveTab: () => void;
    onStartEditing: () => void;
    onEditingContentChange: (nextContent: string) => void;
    onCancelEditing: () => void;
    onSaveEditing: () => void;
    onAcceptAgentProposal: (proposal: AgentProposal) => Promise<void>;
    onSaveAgentProposal: (proposal: AgentProposal) => void;
    onRejectAgentProposal: (parentId: string) => void;
    onClearAgentProposals: () => void;
};

export default function ModificationPanel({
    files,
    activeTab,
    activeTabState,
    openTabs,
    isLoadingFiles,
    editingContent,
    isEditing,
    isSaving,
    isDirty,
    saveError,
    isEditMode,
    selectedFileIds,
    isAgentGenerating,
    agentProposals,
    agentAcceptedMap,
    agentSavedIds,
    agentRejectedIds,
    agentSavingIds,
    agentError,
    agentIntention,
    onRefreshDocuments,
    onClose,
    onTabSelect,
    onTabClose,
    onLoadMoreActiveTab,
    onStartEditing,
    onEditingContentChange,
    onCancelEditing,
    onSaveEditing,
    onAcceptAgentProposal,
    onSaveAgentProposal,
    onRejectAgentProposal,
    onClearAgentProposals,
}: ModificationPanelProps) {
    const contentRef = useRef<HTMLDivElement | null>(null);

    const fullDocumentContent = (activeTabState?.chunks ?? [])
        .map((chunk) => chunk.content)
        .join("\n\n")
        .trim();

    const handleContentScroll = () => {
        if (!contentRef.current || !activeTabState || activeTabState.isLoading || !activeTabState.hasMore) return;
        const { scrollTop, scrollHeight, clientHeight } = contentRef.current;
        if (scrollHeight - scrollTop - clientHeight < 120) void onLoadMoreActiveTab();
    };

    const editScopeLabel = selectedFileIds.size > 0
        ? `${selectedFileIds.size} file(s) selected`
        : "All files";

    const showAgentSection = isEditMode && (
        isAgentGenerating ||
        agentProposals.length > 0 ||
        agentError !== null ||
        !activeTab
    );

    return (
        <aside className="modification-panel">

            {/* ── Header ── */}
            <div className="mod-panel-header">
                <div className="mod-panel-header-title">
                    <h3>Full View</h3>
                    {isEditMode && (
                        <span className="mod-panel-edit-mode-badge">
                            ✏️ Edit — {editScopeLabel}
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

            {/* ── Tabs ── */}
            <div className="mod-panel-tabs" role="tablist" aria-label="Opened documents">
                {openTabs.length === 0 ? (
                    <div className="mod-panel-empty">Open a file from the sidebar to view full content.</div>
                ) : (
                    openTabs.map((fileId) => {
                        const fileName = files.find((f) => f.fileId === fileId)?.fileName ?? fileId;
                        return (
                        <div key={fileId} className={`mod-panel-tab ${activeTab === fileId ? "active" : ""}`}>
                            <button className="mod-panel-tab-label" onClick={() => void onTabSelect(fileId)} type="button">
                                {fileName}
                            </button>
                            <button className="mod-panel-tab-close" onClick={() => onTabClose(fileId)} aria-label={`Close ${fileName}`} type="button">
                                ×
                            </button>
                        </div>
                        );
                    })
                )}
            </div>

            {/* ── Scrollable content ── */}
            <div className="mod-panel-content" ref={contentRef} onScroll={handleContentScroll}>

                {/* ── Agent proposals ── */}
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
                                        : "Will search all files — or check files in sidebar to narrow scope."}
                                </em>
                            </div>
                        )}

                        {agentProposals.map((proposal) => {
                            const isAccepted = agentAcceptedMap.has(proposal.parentId);
                            const isSaved = agentSavedIds.has(proposal.parentId);
                            const isRejected = agentRejectedIds.has(proposal.parentId);
                            const isSaving = agentSavingIds.has(proposal.parentId);
                            const isPending = !isAccepted && !isRejected;

                            return (
                                <div
                                    key={proposal.parentId}
                                    className={`agent-proposal-card ${isAccepted && !isSaved ? "previewing" : ""} ${isSaved ? "saved" : ""} ${isRejected ? "rejected" : ""}`}
                                >
                                    {/* Header */}
                                    <div className="agent-proposal-header">
                                        <span className="agent-proposal-filename">📄 {proposal.fileName}</span>
                                        {isSaved && <span className="agent-proposal-status saved">✓ Saved to database</span>}
                                        {isAccepted && !isSaved && <span className="agent-proposal-status previewing">👁 Previewing — not saved yet</span>}
                                        {isRejected && <span className="agent-proposal-status rejected">✗ Rejected</span>}
                                    </div>

                                    {/* Before / After diff — hidden after accept since full doc is visible in tab */}
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

                                    {/* After accept: remind user the full doc is visible in the tab above */}
                                    {isAccepted && !isSaved && (
                                        <div className="agent-preview-hint">
                                            Changes applied locally. Review the full document in the tab above, then save when ready.
                                        </div>
                                    )}

                                    {/* Actions */}
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
                                                disabled={isSaving}
                                            >
                                                {isSaving ? "Saving..." : "Save to database"}
                                            </button>
                                            <button
                                                className="cancel-btn"
                                                type="button"
                                                onClick={() => onRejectAgentProposal(proposal.parentId)}
                                                disabled={isSaving}
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

                {/* ── Document view ── */}
                {!activeTab ? (
                    !isEditMode && <div className="mod-panel-empty">No file tab selected.</div>
                ) : activeTabState?.error ? (
                    <div className="mod-panel-empty">{activeTabState.error}</div>
                ) : activeTabState?.chunks.length ? (
                    <>
                        <section className="mod-panel-document-window">
                            <div className="preview-header">
                                <h4>Full Text</h4>
                                {!isEditing && (
                                    <button
                                        className="edit-btn"
                                        type="button"
                                        onClick={onStartEditing}
                                        disabled={isSaving || activeTabState.isLoading}
                                    >
                                        Edit
                                    </button>
                                )}
                            </div>

                            {isEditing ? (
                                <>
                                    <textarea
                                        className="edit-textarea"
                                        value={editingContent}
                                        onChange={(e) => onEditingContentChange(e.target.value)}
                                        disabled={isSaving}
                                        rows={20}
                                    />
                                    <div className="edit-actions">
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
                                <pre className="mod-panel-document-text">{fullDocumentContent}</pre>
                            )}

                            {saveError && <div className="mod-panel-save-error">{saveError}</div>}
                        </section>

                        {activeTabState.isLoading && (
                            <div className="mod-panel-loading">Loading more chunks...</div>
                        )}
                        {!activeTabState.hasMore && (
                            <div className="mod-panel-end">End of document</div>
                        )}
                    </>
                ) : activeTabState?.isLoading ? (
                    <div className="mod-panel-loading">Loading full content...</div>
                ) : (
                    <div className="mod-panel-empty">No content available for this file.</div>
                )}

            </div>
        </aside>
    );
}
