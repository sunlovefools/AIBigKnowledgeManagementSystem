import { useRef } from "react";
import type { DiffSegment, FileTabState } from "../types";

// Type definitions for the ModificationPanel component props
type ModificationPanelProps = {
    activeTab: string | null;
    activeTabState: FileTabState | null;
    openTabs: string[];
    isLoadingFiles: boolean;
    editingContent: string;
    isEditing: boolean;
    isSaving: boolean;
    isDirty: boolean;
    saveError: string | null;
    aiEditSummary: string | null;
    aiEditWarnings: string[];
    aiEditDiffSegments: DiffSegment[];
    aiEditError: string | null;
    aiBatchSelectionMode: "manual" | "auto" | null;
    aiBatchPreviewItems: Array<{
        fileName: string;
        ok: boolean;
        score: number | null;
        reasons: string[];
        summary: string | null;
        warnings: string[];
        error: string | null;
        diffSegments: DiffSegment[];
        decision: "pending" | "accepted" | "rejected";
        saveState: "idle" | "saving" | "saved" | "failed";
    }>;
    isSavingAiBatch: boolean;
    aiBatchSaveMessage: string | null;
    aiBatchSaveError: string | null;
    hasAiEditProposal: boolean;
    isAiEditGenerating: boolean;
    onRefreshDocuments: () => void;
    onClose: () => void;
    onTabSelect: (fileName: string) => void;
    onTabClose: (fileName: string) => void;
    onLoadMoreActiveTab: () => void;
    onStartEditing: () => void;
    onEditingContentChange: (nextContent: string) => void;
    onCancelEditing: () => void;
    onSaveEditing: () => void;
    onAcceptAiEdit: () => boolean;
    onRejectAiEdit: () => void;
    onAcceptAiBatchFile: (fileName: string) => boolean;
    onRejectAiBatchFile: (fileName: string) => void;
    onSaveAcceptedBatchFiles: () => void;
    onRetryFailedBatchFiles: () => void;
};

export default function ModificationPanel({
    activeTab,
    activeTabState,
    openTabs,
    isLoadingFiles,
    editingContent,
    isEditing,
    isSaving,
    isDirty,
    saveError,
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
    isAiEditGenerating,
    onRefreshDocuments,
    onClose,
    onTabSelect,
    onTabClose,
    onLoadMoreActiveTab,
    onStartEditing,
    onEditingContentChange,
    onCancelEditing,
    onSaveEditing,
    onAcceptAiEdit,
    onRejectAiEdit,
    onAcceptAiBatchFile,
    onRejectAiBatchFile,
    onSaveAcceptedBatchFiles,
    onRetryFailedBatchFiles,
}: ModificationPanelProps) {
    const contentRef = useRef<HTMLDivElement | null>(null);
    const showSingleAiPreview = aiBatchPreviewItems.length === 0;

    const fullDocumentContent = (activeTabState?.chunks ?? [])
        .map((chunk) => chunk.content)
        .join("\n\n")
        .trim();

    const handleContentScroll = () => {
        if (!contentRef.current || !activeTabState || activeTabState.isLoading || !activeTabState.hasMore) {
            return;
        }

        const { scrollTop, scrollHeight, clientHeight } = contentRef.current;
        const remaining = scrollHeight - scrollTop - clientHeight;

        if (remaining < 120) {
            void onLoadMoreActiveTab();
        }
    };

    return (
        <aside className="modification-panel">
            <div className="mod-panel-header">
                <h3>Full View</h3>
                <div className="mod-panel-header-actions">
                    <button
                        className="mod-panel-refresh-btn"
                        onClick={onRefreshDocuments}
                        disabled={isLoadingFiles}
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
                        onClick={onClose}
                        aria-label="Close modifications panel"
                    >
                        x
                    </button>
                </div>
            </div>

            <div className="mod-panel-tabs" role="tablist" aria-label="Opened documents">
                {openTabs.length === 0 ? (
                    <div className="mod-panel-empty">Open a file from the sidebar to view full content.</div>
                ) : (
                    openTabs.map((fileName) => (
                        <div
                            key={fileName}
                            className={`mod-panel-tab ${activeTab === fileName ? "active" : ""}`}
                        >
                            <button
                                className="mod-panel-tab-label"
                                onClick={() => void onTabSelect(fileName)}
                                type="button"
                            >
                                {fileName}
                            </button>
                            <button
                                className="mod-panel-tab-close"
                                onClick={() => onTabClose(fileName)}
                                aria-label={`Close ${fileName}`}
                                type="button"
                            >
                                ×
                            </button>
                        </div>
                    ))
                )}
            </div>

            <div className="mod-panel-content" ref={contentRef} onScroll={handleContentScroll}>
                <section className="mod-panel-document-window">
                    <div className="preview-header">
                        <h4>Full Text</h4>
                        {!isEditing && activeTabState?.chunks.length ? (
                            <button
                                className="edit-btn"
                                type="button"
                                onClick={onStartEditing}
                                disabled={isSaving || activeTabState.isLoading}
                            >
                                Edit
                            </button>
                        ) : null}
                    </div>

                    {!activeTab ? (
                        <div className="mod-panel-empty">No file tab selected.</div>
                    ) : activeTabState?.error ? (
                        <div className="mod-panel-empty">{activeTabState.error}</div>
                    ) : activeTabState?.chunks.length ? (
                        <>
                            {isEditing ? (
                                <>
                                    <textarea
                                        className="edit-textarea"
                                        value={editingContent}
                                        onChange={(event) => onEditingContentChange(event.target.value)}
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

                    {saveError && <div className="mod-panel-save-error">{saveError}</div>}
                </section>

                {showSingleAiPreview && (
                    <section className="mod-panel-ai-preview-window">
                        <div className="preview-header">
                            <h4>AI Edit Preview</h4>
                        </div>

                        {isAiEditGenerating && (
                            <div className="mod-panel-loading">Generating AI preview...</div>
                        )}

                        {aiEditError && <div className="mod-panel-save-error">{aiEditError}</div>}

                        {hasAiEditProposal && aiEditSummary && (
                            <div className="ai-preview-summary">{aiEditSummary}</div>
                        )}

                        {hasAiEditProposal && aiEditWarnings.length > 0 && (
                            <ul className="ai-preview-warnings">
                                {aiEditWarnings.map((warning, index) => (
                                    <li key={`${warning}-${index}`}>{warning}</li>
                                ))}
                            </ul>
                        )}

                        {hasAiEditProposal ? (
                            <pre className="ai-diff-view">
                                {aiEditDiffSegments.map((segment, index) => (
                                    <div
                                        key={`${segment.type}-${index}-${segment.text.slice(0, 12)}`}
                                        className={`ai-diff-line ai-diff-${segment.type}`}
                                    >
                                        {segment.type === "add" ? "+ " : segment.type === "del" ? "- " : "  "}
                                        {segment.text}
                                    </div>
                                ))}
                            </pre>
                        ) : (
                            <div className="mod-panel-empty">
                                Submit an instruction in chat to generate a preview.
                            </div>
                        )}

                        <div className="ai-preview-actions">
                            <button
                                className="save-btn"
                                type="button"
                                onClick={onAcceptAiEdit}
                                disabled={!hasAiEditProposal || isSaving}
                            >
                                Accept
                            </button>
                            <button
                                className="cancel-btn"
                                type="button"
                                onClick={onRejectAiEdit}
                                disabled={!hasAiEditProposal || isSaving}
                            >
                                Reject
                            </button>
                        </div>
                    </section>
                )}

                {aiBatchPreviewItems.length > 0 && (
                    <section className="mod-panel-ai-preview-window">
                        <div className="preview-header">
                            <h4>Batch File Diffs</h4>
                        </div>
                        <div className="ai-preview-summary">
                            Mode: {aiBatchSelectionMode === "manual" ? "Manual" : "Auto"} · Files: {aiBatchPreviewItems.length}
                        </div>

                        <div className="batch-preview-toolbar">
                            <button
                                className="save-btn"
                                type="button"
                                onClick={onSaveAcceptedBatchFiles}
                                disabled={isSavingAiBatch}
                            >
                                {isSavingAiBatch ? "Saving accepted files..." : "Save accepted files"}
                            </button>
                            <button
                                className="cancel-btn"
                                type="button"
                                onClick={onRetryFailedBatchFiles}
                                disabled={isSavingAiBatch}
                            >
                                Reset failed saves
                            </button>
                        </div>

                        {aiBatchSaveMessage && <div className="ai-preview-summary">{aiBatchSaveMessage}</div>}
                        {aiBatchSaveError && <div className="mod-panel-save-error">{aiBatchSaveError}</div>}

                        <div className="batch-diff-list">
                            {aiBatchPreviewItems.map((item) => (
                                <article key={item.fileName} className="batch-diff-card">
                                    <div className="batch-diff-header">
                                        <span className="batch-diff-file">{item.fileName}</span>
                                        <div className="batch-diff-header-right">
                                            {item.ok && item.saveState !== "idle" && (
                                                <span className={`batch-diff-save-state ${item.saveState}`}>
                                                    {item.saveState === "saving"
                                                        ? "Saving"
                                                        : item.saveState === "saved"
                                                            ? "Saved"
                                                            : "Save failed"}
                                                </span>
                                            )}
                                            {item.ok && item.decision !== "pending" && (
                                                <span className={`batch-diff-decision ${item.decision}`}>
                                                    {item.decision === "accepted" ? "Accepted" : "Rejected"}
                                                </span>
                                            )}
                                            <span className={`batch-diff-status ${item.ok ? "ok" : "error"}`}>
                                                {item.ok ? "Success" : "Failed"}
                                            </span>
                                        </div>
                                    </div>

                                    {item.reasons.length > 0 && (
                                        <div className="batch-diff-reasons">
                                            {item.reasons.join(" · ")}
                                        </div>
                                    )}

                                    {item.ok ? (
                                        <>
                                            {item.summary && <div className="ai-preview-summary">{item.summary}</div>}
                                            {item.warnings.length > 0 && (
                                                <ul className="ai-preview-warnings">
                                                    {item.warnings.map((warning, index) => (
                                                        <li key={`${item.fileName}-warning-${index}`}>{warning}</li>
                                                    ))}
                                                </ul>
                                            )}

                                            <pre className="ai-diff-view batch-diff-view">
                                                {item.diffSegments.map((segment, index) => (
                                                    <div
                                                        key={`${item.fileName}-${segment.type}-${index}`}
                                                        className={`ai-diff-line ai-diff-${segment.type}`}
                                                    >
                                                        {segment.type === "add" ? "+ " : segment.type === "del" ? "- " : "  "}
                                                        {segment.text}
                                                    </div>
                                                ))}
                                            </pre>

                                            <div className="batch-diff-actions">
                                                <button
                                                    className="save-btn"
                                                    type="button"
                                                    onClick={() => onAcceptAiBatchFile(item.fileName)}
                                                    disabled={item.decision === "accepted" || isSaving || isSavingAiBatch}
                                                >
                                                    Accept file
                                                </button>
                                                <button
                                                    className="cancel-btn"
                                                    type="button"
                                                    onClick={() => onRejectAiBatchFile(item.fileName)}
                                                    disabled={item.decision === "rejected" || isSaving || isSavingAiBatch}
                                                >
                                                    Reject file
                                                </button>
                                            </div>
                                        </>
                                    ) : (
                                        <div className="mod-panel-save-error">
                                            {item.error || "Failed to generate preview for this file."}
                                        </div>
                                    )}
                                </article>
                            ))}
                        </div>
                    </section>
                )}
            </div>
        </aside>
    );
}
