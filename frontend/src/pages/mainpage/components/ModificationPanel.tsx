import { useEffect, useMemo, useRef } from "react";
import MarkdownEditor from "./FileViewingAndModification";
import type {
    AgentProposal,
    FileTabAsyncState,
    FileTabState,
    HighlightedSelection,
    SidebarFileSummary,
} from "../types";
import { buildChunkRanges } from "../hooks/documents/utils/chunkText";

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
    agentRejectedIds: Set<string>;
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
    onRejectAgentProposal: (parentId: string) => void;
    onClearAgentProposals: () => void;
};

function getContainerElement(node: Node): HTMLElement | null {
    if (node instanceof HTMLElement) return node;
    return node.parentElement;
}

function projectMarkdownToPlain(markdown: string): string {
    const plainChars: string[] = [];
    let index = 0;
    let lineStart = true;
    let inFence = false;

    while (index < markdown.length) {
        if (lineStart) {
            if (markdown.startsWith("```", index)) {
                const newlineIndex = markdown.indexOf("\n", index);
                if (newlineIndex === -1) break;
                inFence = !inFence;
                plainChars.push("\n");
                index = newlineIndex + 1;
                lineStart = true;
                continue;
            }

            if (!inFence) {
                const leadingSpacesMatch = markdown.slice(index).match(/^[ ]{0,3}/);
                const leadingSpaces = leadingSpacesMatch ? leadingSpacesMatch[0] : "";
                const markerStart = index + leadingSpaces.length;
                let markerConsumed = 0;

                if (markerStart < markdown.length && markdown[markerStart] === ">") {
                    markerConsumed = 1;
                    if (
                        markerStart + markerConsumed < markdown.length &&
                        markdown[markerStart + markerConsumed] === " "
                    ) {
                        markerConsumed += 1;
                    }
                } else {
                    const headingMatch = markdown.slice(markerStart).match(/^#{1,6}[ \t]+/);
                    const unorderedListMatch = markdown.slice(markerStart).match(/^[-*+][ \t]+/);
                    const orderedListMatch = markdown.slice(markerStart).match(/^\d+[.)][ \t]+/);
                    if (headingMatch) markerConsumed = headingMatch[0].length;
                    else if (unorderedListMatch) markerConsumed = unorderedListMatch[0].length;
                    else if (orderedListMatch) markerConsumed = orderedListMatch[0].length;
                }

                if (markerConsumed > 0) {
                    index = markerStart + markerConsumed;
                    lineStart = false;
                    continue;
                }
            }
        }

        const current = markdown[index];
        if (!inFence && (markdown.startsWith("**", index) || markdown.startsWith("__", index) || markdown.startsWith("~~", index))) {
            index += 2;
            continue;
        }
        if (!inFence && (current === "*" || current === "_" || current === "`")) {
            index += 1;
            continue;
        }
        if (current === "\\" && index + 1 < markdown.length) {
            plainChars.push(markdown[index + 1]);
            lineStart = markdown[index + 1] === "\n";
            index += 2;
            continue;
        }

        plainChars.push(current);
        lineStart = current === "\n";
        index += 1;
    }

    return plainChars.join("");
}

function findNearestOccurrence(haystack: string, needle: string, expectedOffset: number): number {
    if (!needle) return -1;
    const first = haystack.indexOf(needle);
    if (first === -1) return -1;

    let best = first;
    let bestDistance = Math.abs(first - expectedOffset);
    let cursor = first;
    while (cursor !== -1) {
        const next = haystack.indexOf(needle, cursor + 1);
        if (next === -1) break;
        const distance = Math.abs(next - expectedOffset);
        if (distance < bestDistance) {
            best = next;
            bestDistance = distance;
        }
        cursor = next;
    }
    return best;
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
    agentRejectedIds,
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
    onRejectAgentProposal,
    onClearAgentProposals,
}: ModificationPanelProps) {
    const contentRef = useRef<HTMLDivElement | null>(null);
    const previousProposalCountRef = useRef(0);

    const isDeletingActiveFile = Boolean(activeTab && deletingFileId === activeTab);
    const activeDocumentView = useMemo(
        () => buildChunkRanges(activeTabData?.chunks ?? []),
        [activeTabData?.chunks]
    );

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

    // Handler to detect the user's text selection within the document for selection-based edits.
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
        const textRoot = contentRef.current?.querySelector<HTMLElement>(".mod-panel-document-text");

        if (
            !textRoot ||
            !startElement ||
            !endElement ||
            !textRoot.contains(startElement) ||
            !textRoot.contains(endElement)
        ) {
            onHighlightedSelectionChange(null);
            onSelectionErrorChange(null);
            return;
        }

        const prefixRange = range.cloneRange();
        prefixRange.selectNodeContents(textRoot);
        prefixRange.setEnd(range.startContainer, range.startOffset);

        const selectedText = range.toString();
        const viewStartOffset = prefixRange.toString().length;
        const viewEndOffset = viewStartOffset + selectedText.length;
        const plainChunkTexts = activeTabData.chunks.map((chunk) => projectMarkdownToPlain(chunk.content));
        const plainFullText = plainChunkTexts.join("\n\n");

        let resolvedStartOffset = viewStartOffset;
        let resolvedEndOffset = viewEndOffset;
        if (plainFullText.slice(resolvedStartOffset, resolvedEndOffset) !== selectedText) {
            const nearestStart = findNearestOccurrence(plainFullText, selectedText, viewStartOffset);
            if (nearestStart === -1) {
                onSelectionErrorChange("The current selection does not match the stored chunk content.");
                onHighlightedSelectionChange(null);
                selection.removeAllRanges();
                return;
            }
            resolvedStartOffset = nearestStart;
            resolvedEndOffset = nearestStart + selectedText.length;
        }

        let cursor = 0;
        const plainRanges = plainChunkTexts.map((chunkText, index) => {
            const start = cursor;
            const end = start + chunkText.length;
            cursor = end;
            if (index < plainChunkTexts.length - 1) cursor += 2;
            return { start, end };
        });

        const touchedRangeIndexes = plainRanges
            .map((chunkRange, index) =>
                chunkRange.start < resolvedEndOffset && resolvedStartOffset < chunkRange.end ? index : -1
            )
            .filter((index) => index >= 0);
        if (touchedRangeIndexes.length === 0) {
            onSelectionErrorChange("The current selection is outside known chunk boundaries.");
            onHighlightedSelectionChange(null);
            selection.removeAllRanges();
            return;
        }

        const firstTouchedIndex = touchedRangeIndexes[0];
        const lastTouchedIndex = touchedRangeIndexes[touchedRangeIndexes.length - 1];
        const firstTouchedRange = plainRanges[firstTouchedIndex];
        const rangeLocalStartOffset = resolvedStartOffset - firstTouchedRange.start;
        const rangeLocalEndOffset = resolvedEndOffset - firstTouchedRange.start;

        const fileName = files.find((file) => file.fileId === activeTab)?.fileName ?? activeTab;
        onSelectionErrorChange(null);
        onHighlightedSelectionChange({
            fileId: activeTab,
            fileName,
            selectedText,
            startOffset: rangeLocalStartOffset,
            endOffset: rangeLocalEndOffset,
            startChunkNumber: firstTouchedIndex + 1,
            endChunkNumber: lastTouchedIndex + 1,
        });
    };

    const editScopeLabel = selectedFileIds.size > 0
        ? `${selectedFileIds.size} file(s) selected`
        : "All files";
    const activeFileName = activeTab
        ? files.find((file) => file.fileId === activeTab)?.fileName ?? activeTab
        : "No file selected";

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
                            const isRejected = agentRejectedIds.has(proposal.parentId);
                            const isPending = !isAccepted && !isRejected;

                            return (
                                <div
                                    key={`${proposal.parentId}-${proposal.selectionStart ?? "full"}`}
                                    className={`agent-proposal-card ${isAccepted ? "previewing" : ""} ${isRejected ? "rejected" : ""}`}
                                >
                                    <div className="agent-proposal-header">
                                        <span className="agent-proposal-filename">{proposal.fileName}</span>
                                        <div className="agent-proposal-meta">
                                            {proposal.source === "selection" && (
                                                <span className="agent-proposal-source">Selection</span>
                                            )}
                                            {isAccepted && <span className="agent-proposal-status previewing">Applied to draft</span>}
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

                                    {isAccepted && (
                                        <div className="agent-preview-hint">
                                            Changes applied to the draft. Use the document Save button above to persist.
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

                                    {isAccepted && (
                                        <div className="ai-preview-actions">
                                            <button
                                                className="cancel-btn"
                                                type="button"
                                                onClick={() => onRejectAgentProposal(proposal.parentId)}
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
                ) : activeTabData?.chunks.length ? (
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
                                    Highlight text to edit directly.
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
                                    className={`mod-panel-document-flow ${highlightedSelection ? "selection-active" : ""}`}
                                    onMouseUp={handleDocumentSelection}
                                    onKeyUp={handleDocumentSelection}
                                >
                                    <div className="mod-panel-document-text">
                                        <MarkdownEditor
                                            markdown={activeDocumentView.fullText}
                                            editable={false}
                                            className="mod-panel-segment-editor"
                                        />
                                    </div>
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
