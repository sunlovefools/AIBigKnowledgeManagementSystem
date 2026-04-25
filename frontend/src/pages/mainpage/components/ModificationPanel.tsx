import { useEffect, useMemo, useRef } from "react";
import MarkdownEditor, { type MarkdownReviewMarker } from "./FileViewingAndModification";
import type {
    AgentProposal,
    FileTabAsyncState,
    FileTabState,
    HighlightedSelection,
    ResolvedProposalMarker,
    SidebarFileSummary,
} from "../types";
import { buildChunkRanges } from "../hooks/documents/utils/chunkText";
import { buildInlineDiffTokens, buildProposalHunks } from "../hooks/documents/utils/inlineDiff";

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
    focusedProposalKey?: string | null;
    onFocusedProposalHandled?: () => void;
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
    onUndoAgentProposal: (parentId: string) => void;
    onAcceptActiveFileProposals: (fileId: string) => Promise<void>;
    onRejectActiveFileProposals: (fileId: string) => void;
    onClearAgentProposals: () => void;
};

function getContainerElement(node: Node): HTMLElement | null {
    return node instanceof HTMLElement ? node : node.parentElement;
}

function getProposalKey(proposal: AgentProposal): string {
    return `${proposal.parentId}-${proposal.selectionStart ?? "full"}`;
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

function projectMarkdownToPlain(markdown: string): string {
    const plainChars: string[] = [];
    let index = 0;
    let lineStart = true;
    let inFence = false;
    while (index < markdown.length) {
        if (lineStart && markdown.startsWith("```", index)) {
            const newlineIndex = markdown.indexOf("\n", index);
            if (newlineIndex === -1) break;
            inFence = !inFence;
            plainChars.push("\n");
            index = newlineIndex + 1;
            continue;
        }
        if (lineStart && !inFence) {
            const leadingSpaces = (markdown.slice(index).match(/^[ ]{0,3}/) ?? [""])[0];
            const markerStart = index + leadingSpaces.length;
            const markerMatch = markdown.slice(markerStart).match(/^(>|#{1,6}[ \t]+|[-*+][ \t]+|\d+[.)][ \t]+)/);
            if (markerMatch) {
                index = markerStart + markerMatch[0].length;
                lineStart = false;
                continue;
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
    focusedProposalKey = null,
    onFocusedProposalHandled,
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
    onUndoAgentProposal,
    onAcceptActiveFileProposals,
    onRejectActiveFileProposals,
    onClearAgentProposals,
}: ModificationPanelProps) {
    const contentRef = useRef<HTMLDivElement | null>(null);
    const previousProposalCountRef = useRef(0);
    const isDeletingActiveFile = Boolean(activeTab && deletingFileId === activeTab);
    const activeDocumentView = useMemo(() => buildChunkRanges(activeTabData?.chunks ?? []), [activeTabData?.chunks]);
    const activePlainDocumentView = useMemo(
        () => buildChunkRanges((activeTabData?.chunks ?? []).map((chunk) => ({
            ...chunk,
            content: projectMarkdownToPlain(chunk.content),
        }))),
        [activeTabData?.chunks]
    );
    const activeFileProposals = useMemo(
        () => (activeTab ? agentProposals.filter((proposal) => proposal.fileId === activeTab) : []),
        [activeTab, agentProposals]
    );
    const reviewBaseText = isEditing ? editingContent : activeDocumentView.fullText;
    const reviewBasePlainText = useMemo(() => projectMarkdownToPlain(reviewBaseText), [reviewBaseText]);
    const resolvedInlineMarkers = useMemo<ResolvedProposalMarker[]>(() => {
        if (!activeTab || !activeTabData?.chunks.length || !activeFileProposals.length) return [];
        const baselineText = activePlainDocumentView.fullText;
        const acceptedEntries = Array.from(agentAcceptedMap.values()).filter((entry) => entry.fileId === activeTab);
        const markers: ResolvedProposalMarker[] = [];
        for (const proposal of activeFileProposals) {
            if (agentRejectedIds.has(proposal.parentId) && !agentAcceptedMap.has(proposal.parentId)) continue;
            const accepted = agentAcceptedMap.get(proposal.parentId);
            const status = accepted ? "accepted" : "pending";
            const plainOriginal = projectMarkdownToPlain(proposal.original);
            const plainProposed = projectMarkdownToPlain(proposal.proposed);
            const hunks = buildProposalHunks(plainOriginal, plainProposed);
            if (!hunks.length) continue;
            let baselineOffset = -1;
            if (proposal.source === "selection" && proposal.selectionStart !== undefined) {
                baselineOffset = findNearestOccurrence(baselineText, plainOriginal, proposal.selectionStart);
            } else {
                const targetRange = activePlainDocumentView.ranges.find((range) => range.parentId === proposal.parentId);
                const targetChunk = activeTabData.chunks.find((chunk) => chunk.parentId === proposal.parentId);
                const targetChunkPlainText = projectMarkdownToPlain(targetChunk?.content ?? "");
                const chunkOffset = targetChunkPlainText.indexOf(plainOriginal);
                baselineOffset = targetRange && chunkOffset >= 0
                    ? targetRange.start + chunkOffset
                    : findNearestOccurrence(baselineText, plainOriginal, targetRange?.start ?? 0);
            }
            if (baselineOffset < 0) continue;
            const priorDelta = acceptedEntries
                .filter((entry) => entry.parentId !== proposal.parentId && typeof entry.patchBaselineOffset === "number" && entry.patchBaselineOffset < baselineOffset)
                .reduce((sum, entry) => sum + (projectMarkdownToPlain(entry.proposed).length - projectMarkdownToPlain(entry.original).length), 0);
            const expectedSourceText = status === "accepted" ? plainProposed : plainOriginal;
            let offset = status === "accepted" && accepted?.patchOffset !== undefined
                ? findNearestOccurrence(reviewBasePlainText, plainProposed, accepted.patchOffset)
                : baselineOffset + priorDelta;
            if (reviewBasePlainText.slice(offset, offset + expectedSourceText.length) !== expectedSourceText) {
                offset = findNearestOccurrence(reviewBasePlainText, expectedSourceText, offset);
            }
            if (offset < 0) continue;
            markers.push({
                proposalKey: getProposalKey(proposal),
                parentId: proposal.parentId,
                fileId: proposal.fileId,
                fileName: proposal.fileName,
                offset,
                baselineOffset,
                sourceLength: expectedSourceText.length,
                replacementLength: plainProposed.length,
                status,
                proposal,
                tokens: buildInlineDiffTokens(plainOriginal, plainProposed),
                hunks,
            });
        }
        return markers.sort((left, right) => left.offset - right.offset);
    }, [activeFileProposals, activePlainDocumentView.fullText, activePlainDocumentView.ranges, activeTab, activeTabData?.chunks, agentAcceptedMap, agentRejectedIds, reviewBasePlainText]);
    const hasInlineReview = resolvedInlineMarkers.length > 0;
    const pendingCount = resolvedInlineMarkers.filter((marker) => marker.status === "pending").length;
    const acceptedCount = resolvedInlineMarkers.filter((marker) => marker.status === "accepted").length;
    const reviewMarkers = useMemo<MarkdownReviewMarker[]>(
        () => resolvedInlineMarkers.map((marker) => ({
            proposalKey: marker.proposalKey,
            parentId: marker.parentId,
            status: marker.status,
            hunks: marker.hunks,
            offset: marker.offset,
        })),
        [resolvedInlineMarkers]
    );
    const showAgentSection = isEditMode && (isAgentGenerating || agentProposals.length > 0 || agentError !== null || !activeTab);

    useEffect(() => {
        const previousCount = previousProposalCountRef.current;
        if (agentProposals.length > 0 && agentProposals.length !== previousCount) {
            contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
        }
        previousProposalCountRef.current = agentProposals.length;
    }, [agentProposals.length]);

    useEffect(() => {
        if (!focusedProposalKey || !contentRef.current) return;
        const marker = contentRef.current.querySelector<HTMLElement>(`.review-suggestion-widget[data-proposal-key="${focusedProposalKey}"], .review-text-marker[data-proposal-key="${focusedProposalKey}"]`);
        if (!marker) return;
        marker.scrollIntoView({ behavior: "smooth", block: "center" });
        marker.classList.add("focused");
        const timerId = window.setTimeout(() => marker.classList.remove("focused"), 1800);
        onFocusedProposalHandled?.();
        return () => window.clearTimeout(timerId);
    }, [focusedProposalKey, onFocusedProposalHandled, resolvedInlineMarkers]);

    const handleContentScroll = () => {
        if (!contentRef.current || !activeTabData || activeTabAsync?.isLoading || !activeTabData.hasMore) return;
        const { scrollTop, scrollHeight, clientHeight } = contentRef.current;
        if (scrollHeight - scrollTop - clientHeight < 120) void onLoadMoreActiveTab();
    };

    const handleDocumentSelection = () => {
        if (!isEditMode || isEditing || hasInlineReview || !activeTab || !activeTabData?.chunks.length) return;
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
        if (!textRoot || !startElement || !endElement || !textRoot.contains(startElement) || !textRoot.contains(endElement)) {
            onHighlightedSelectionChange(null);
            onSelectionErrorChange(null);
            return;
        }
        const prefixRange = range.cloneRange();
        prefixRange.selectNodeContents(textRoot);
        prefixRange.setEnd(range.startContainer, range.startOffset);
        const selectedText = range.toString();
        const viewStartOffset = prefixRange.toString().length;
        const plainChunkTexts = activeTabData.chunks.map((chunk) => projectMarkdownToPlain(chunk.content));
        const plainFullText = plainChunkTexts.join("\n\n");
        let resolvedStartOffset = viewStartOffset;
        if (plainFullText.slice(resolvedStartOffset, resolvedStartOffset + selectedText.length) !== selectedText) {
            resolvedStartOffset = findNearestOccurrence(plainFullText, selectedText, viewStartOffset);
            if (resolvedStartOffset < 0) {
                onSelectionErrorChange("The current selection does not match the stored chunk content.");
                onHighlightedSelectionChange(null);
                selection.removeAllRanges();
                return;
            }
        }
        let cursor = 0;
        const ranges = plainChunkTexts.map((chunkText, index) => {
            const start = cursor;
            const end = start + chunkText.length;
            cursor = end + (index < plainChunkTexts.length - 1 ? 2 : 0);
            return { start, end };
        });
        const touched = ranges.map((item, index) => (item.start < resolvedStartOffset + selectedText.length && resolvedStartOffset < item.end ? index : -1)).filter((index) => index >= 0);
        if (!touched.length) {
            onSelectionErrorChange("The current selection is outside known chunk boundaries.");
            onHighlightedSelectionChange(null);
            selection.removeAllRanges();
            return;
        }
        const firstRange = ranges[touched[0]];
        const fileName = files.find((file) => file.fileId === activeTab)?.fileName ?? activeTab;
        onSelectionErrorChange(null);
        onHighlightedSelectionChange({
            fileId: activeTab,
            fileName,
            selectedText,
            startOffset: resolvedStartOffset - firstRange.start,
            endOffset: resolvedStartOffset - firstRange.start + selectedText.length,
            startChunkNumber: touched[0] + 1,
            endChunkNumber: touched[touched.length - 1] + 1,
        });
    };

    const editScopeLabel = selectedFileIds.size > 0 ? `${selectedFileIds.size} file(s) selected` : "All files";
    const activeFileName = activeTab ? files.find((file) => file.fileId === activeTab)?.fileName ?? activeTab : "No file selected";
    const hasUnresolvedSuggestions = pendingCount > 0;

    return (
        <aside className="modification-panel">
            {!hideTabs && (
                <div className="mod-panel-tabs" role="tablist" aria-label="Opened documents">
                    {openTabs.length === 0 ? <div className="mod-panel-tabs-empty">Open a file from the sidebar to view full content.</div> : openTabs.map((fileId) => {
                        const fileName = files.find((entry) => entry.fileId === fileId)?.fileName ?? fileId;
                        return (
                            <div key={fileId} className={`mod-panel-tab ${activeTab === fileId ? "active" : ""}`}>
                                <button className="mod-panel-tab-label" onClick={() => void onTabSelect(fileId)} type="button">{fileName}</button>
                                <button className="mod-panel-tab-close" onClick={() => onTabClose(fileId)} aria-label={`Close ${fileName}`} type="button">x</button>
                            </div>
                        );
                    })}
                </div>
            )}
            {!hideHeader && (
                <div className="mod-panel-header">
                    <div className="mod-panel-header-title"><h3>{activeFileName}</h3>{isEditMode && <span className="mod-panel-edit-mode-badge">Edit - {editScopeLabel}</span>}</div>
                    <div className="mod-panel-header-actions">
                        <button className="mod-panel-refresh-btn" onClick={onRefreshDocuments} disabled={isLoadingFiles} aria-label="Refresh documents" title="Refresh from database">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36M20.49 15a9 9 0 0 1-14.85 3.36" /></svg>
                        </button>
                        <button className="mod-panel-close-btn" onClick={onClose} aria-label="Close modifications panel">x</button>
                    </div>
                </div>
            )}
            <div className="mod-panel-content" ref={contentRef} onScroll={handleContentScroll}>
                {showAgentSection && (
                    <section className="mod-panel-agent-section">
                        <div className="preview-header">
                            <h4>AI Proposals{agentIntention && <span className="agent-intention-badge">{agentIntention}</span>}</h4>
                            {agentProposals.length > 0 && <button className="cancel-btn" type="button" onClick={onClearAgentProposals}>Clear all</button>}
                        </div>
                        {isAgentGenerating && <div className="mod-panel-loading">Agent is searching and generating proposals...</div>}
                        {agentError && <div className="mod-panel-save-error">{agentError}</div>}
                        {!isAgentGenerating && agentProposals.length === 0 && !agentError && (
                            <div className="mod-panel-empty">Type an instruction in the chat to modify documents.<br /><em>{selectedFileIds.size > 0 ? `Will search ${selectedFileIds.size} selected file(s).` : "Will search all files - or check files in sidebar to narrow scope."}</em></div>
                        )}
                        {!isAgentGenerating && activeFileProposals.length > 0 && activeTab && (
                            <div className="inline-review-summary">
                                {pendingCount > 0 && <span>{pendingCount} pending</span>}
                                {acceptedCount > 0 && <span>{acceptedCount} accepted</span>}
                                <span>Review changes inline below.</span>
                                <div className="inline-review-bulk-actions">
                                    <button className="inline-review-bulk-btn accept" type="button" onClick={() => { void onAcceptActiveFileProposals(activeTab); }} disabled={pendingCount === 0}>Accept all in this file</button>
                                    <button className="inline-review-bulk-btn reject" type="button" onClick={() => onRejectActiveFileProposals(activeTab)} disabled={pendingCount === 0}>Reject all in this file</button>
                                </div>
                            </div>
                        )}
                    </section>
                )}
                {!activeTab ? (!isEditMode && <div className="mod-panel-empty">No file tab selected.</div>) : activeTabAsync?.error ? (
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
                                                <button className="save-btn" type="button" onClick={onSaveEditing} disabled={isSaving || !isDirty}>{isSaving ? "Saving..." : "Save"}</button>
                                                <button className="cancel-btn" type="button" onClick={onCancelEditing} disabled={isSaving}>Cancel</button>
                                            </div>
                                        </>
                                    ) : (
                                        <div className="document-action-group">
                                            <button className="edit-btn" type="button" onClick={onStartEditing} disabled={isSaving || isDeletingActiveFile || Boolean(activeTabAsync?.isLoading) || hasUnresolvedSuggestions}>Edit</button>
                                            <button className="delete-btn" type="button" onClick={onDeleteActiveFile} disabled={isSaving || isDeletingActiveFile || Boolean(activeTabAsync?.isLoading)}>{isDeletingActiveFile ? "Deleting..." : "Delete"}</button>
                                        </div>
                                    )}
                                </div>
                            )}
                            {isEditMode && !isEditing && hasUnresolvedSuggestions && <div className="mod-panel-selection-hint">Resolve suggestions first before free editing.</div>}
                            {isEditMode && !isEditing && !hasInlineReview && <div className="mod-panel-selection-hint">Highlight text to edit directly.</div>}
                            {selectionError && <div className="mod-panel-selection-error">{selectionError}</div>}
                            {hasInlineReview ? (
                                <div className="mod-panel-document-flow inline-review-active">
                                    <div className="mod-panel-document-text mod-panel-inline-review">
                                        <MarkdownEditor
                                            markdown={reviewBaseText}
                                            editable={false}
                                            className="mod-panel-segment-editor"
                                            reviewMarkers={reviewMarkers}
                                            reviewCallbacks={{
                                                onAccept: (parentId) => {
                                                    const proposal = resolvedInlineMarkers.find((marker) => marker.parentId === parentId)?.proposal;
                                                    if (proposal) void onAcceptAgentProposal(proposal);
                                                },
                                                onReject: onRejectAgentProposal,
                                                onUndo: onUndoAgentProposal,
                                            }}
                                        />
                                    </div>
                                </div>
                            ) : isEditing ? (
                                <MarkdownEditor markdown={editingContent} editable={!isSaving} onChange={onEditingContentChange} className="mod-panel-active-editor" />
                            ) : (
                                <div className={`mod-panel-document-flow ${highlightedSelection ? "selection-active" : ""}`} onMouseUp={handleDocumentSelection} onKeyUp={handleDocumentSelection}>
                                    <div className="mod-panel-document-text"><MarkdownEditor markdown={activeDocumentView.fullText} editable={false} className="mod-panel-segment-editor" /></div>
                                </div>
                            )}
                            {saveError && <div className="mod-panel-save-error">{saveError}</div>}
                        </section>
                        {activeTabAsync?.isLoading && <div className="mod-panel-loading">Loading more chunks...</div>}
                        {activeTabData && !activeTabData.hasMore && <div className="mod-panel-end">End of document</div>}
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
