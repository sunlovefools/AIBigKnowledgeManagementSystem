import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { HighlightedSelection, PendingModificationNavItem } from "../types";
import type { ModificationAgentMode } from "../hooks/documents/api/documentsApi";

type ChatInputProps = {
    input: string;
    isQuerying: boolean;
    scopeControls?: ReactNode;
    searchScopeLabel?: string;
    isModificationPanelOpen: boolean;
    isEditMode: boolean;
    modificationAgentMode: ModificationAgentMode;
    highlightedSelection: HighlightedSelection | null;
    pendingModificationItems?: PendingModificationNavItem[];
    onInputChange: (value: string) => void;
    onInputKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
    onModificationAgentModeChange: (mode: ModificationAgentMode) => void;
    onToggleModificationPanel: () => void;
    onClearHighlightedSelection: () => void;
    onNavigateToModification?: (fileId: string, proposalKey: string) => void;
    onSend: () => void;
};

export default function ChatInput({
    input,
    isQuerying,
    scopeControls,
    searchScopeLabel = "all collections",
    isModificationPanelOpen,
    isEditMode,
    modificationAgentMode,
    highlightedSelection,
    pendingModificationItems = [],
    onInputChange,
    onInputKeyDown,
    onToggleModificationPanel,
    onClearHighlightedSelection,
    onNavigateToModification,
    onSend,
}: ChatInputProps) {
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);
    const [isPendingTrayOpen, setIsPendingTrayOpen] = useState(false);

    const adjustTextareaHeight = useCallback(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        textarea.style.height = "0px";
        const maxHeight = Number.parseFloat(window.getComputedStyle(textarea).maxHeight) || 200;
        const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
        textarea.style.height = `${nextHeight}px`;
        textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
    }, []);

    useLayoutEffect(() => {
        adjustTextareaHeight();
    }, [input, adjustTextareaHeight]);

    useEffect(() => {
        if (!isEditMode || pendingModificationItems.length === 0) {
            setIsPendingTrayOpen(false);
        }
    }, [isEditMode, pendingModificationItems.length]);

    const placeholder = isEditMode
        ? highlightedSelection
            ? "Describe how to modify the selected text..."
            : "Enter edit instruction..."
        : searchScopeLabel === "All collections"
            ? "Ask across all collections..."
            : `Ask in ${searchScopeLabel}...`;

    const selectionPreview = highlightedSelection?.selectedText.replace(/\s+/g, " ").trim() ?? "";
    const compactSelectionPreview =
        selectionPreview.length > 56 ? `${selectionPreview.slice(0, 56)}...` : selectionPreview || "Selected text";
    const totalPendingChanges = pendingModificationItems.reduce((sum, item) => sum + item.pendingCount, 0);
    const showPendingTray = isEditMode && totalPendingChanges > 0;

    return (
        <div className={`input-area-wrapper ${isEditMode ? "edit-mode-active" : ""}`}>
            <div className={`input-container ${isEditMode ? "edit-mode-active" : ""}`}>
                {scopeControls && <div className="input-search-mode-row">{scopeControls}</div>}
                {showPendingTray && (
                    <div className="input-pending-tray">
                        <button
                            type="button"
                            className={`input-pending-summary ${isPendingTrayOpen ? "open" : ""}`}
                            onClick={() => setIsPendingTrayOpen((prev) => !prev)}
                            aria-expanded={isPendingTrayOpen}
                            aria-label={isPendingTrayOpen ? "Collapse pending changes" : "Expand pending changes"}
                        >
                            <span className="input-pending-summary-label">Pending changes</span>
                            <span className="input-pending-summary-count">{totalPendingChanges}</span>
                            <span className={`input-pending-summary-chevron ${isPendingTrayOpen ? "open" : ""}`} aria-hidden="true">
                                {"\u203A"}
                            </span>
                        </button>

                        {isPendingTrayOpen && (
                            <div className="input-pending-list" role="list" aria-label="Pending changes by file">
                                {pendingModificationItems.map((item) => (
                                    <button
                                        key={item.fileId}
                                        type="button"
                                        className="input-pending-item"
                                        onClick={() => onNavigateToModification?.(item.fileId, item.targetProposalKey)}
                                    >
                                        <span className="input-pending-item-main">
                                            <span className="input-pending-item-file">{item.fileName}</span>
                                            <span className="input-pending-item-count">{item.pendingCount}</span>
                                        </span>
                                        <span className="input-pending-item-arrow" aria-hidden="true">
                                            {"\u203A"}
                                        </span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {highlightedSelection && (
                    <div className="input-selection-chip">
                        <span className="input-selection-chip-file" title={highlightedSelection.fileName}>
                            {highlightedSelection.fileName}
                        </span>
                        <span className="input-selection-chip-separator" aria-hidden="true">
                            :
                        </span>
                        <div className="input-selection-chip-text">
                            {compactSelectionPreview}
                        </div>
                        <button
                            className="input-selection-chip-clear"
                            type="button"
                            onClick={onClearHighlightedSelection}
                            aria-label="Clear selected text"
                        >
                            x
                        </button>
                    </div>
                )}
                <textarea
                    ref={textareaRef}
                    className="chat-input"
                    placeholder={placeholder}
                    rows={1}
                    value={input}
                    onChange={(event) => onInputChange(event.target.value)}
                    onKeyDown={onInputKeyDown}
                />
                <button
                    className={`modification-toggle ${isModificationPanelOpen ? "active" : ""}`}
                    onClick={onToggleModificationPanel}
                    aria-label="Toggle modifications panel"
                    title={isEditMode ? "Exit edit mode" : "Enter edit mode"}
                >
                    <svg
                        width="18"
                        height="18"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    >
                        <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L21 6"></path>
                    </svg>
                </button>
                <button
                    className="send-icon-btn"
                    onClick={onSend}
                    disabled={!input.trim() || isQuerying}
                    aria-label="Send message"
                >
                    <svg
                        width="18"
                        height="18"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                    >
                        <line x1="22" y1="2" x2="11" y2="13"></line>
                        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                    </svg>
                </button>
            </div>
            <div className="input-hint">
                {isEditMode
                    ? highlightedSelection
                        ? "Edit mode - highlighted text will be sent directly to the editor | Enter to send"
                        : `Edit mode - ${modificationAgentMode === "skills" ? "Skills agent" : "Workflow agent"} will modify documents | Enter to send`
                    : "Enter to send | Shift+Enter for a new line"}
            </div>
        </div>
    );
}
