import { useCallback, useLayoutEffect, useRef, type KeyboardEvent } from "react";
import type { HighlightedSelection } from "../types";

type ChatInputProps = {
    input: string;
    isQuerying: boolean;
    isModificationPanelOpen: boolean;
    isEditMode: boolean;
    highlightedSelection: HighlightedSelection | null;
    onInputChange: (value: string) => void;
    onInputKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
    onToggleModificationPanel: () => void;
    onClearHighlightedSelection: () => void;
    onSend: () => void;
};

export default function ChatInput({
    input,
    isQuerying,
    isModificationPanelOpen,
    isEditMode,
    highlightedSelection,
    onInputChange,
    onInputKeyDown,
    onToggleModificationPanel,
    onClearHighlightedSelection,
    onSend,
}: ChatInputProps) {
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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

    const placeholder = isEditMode
        ? highlightedSelection
            ? "Describe how to modify the selected text..."
            : "Enter edit instruction (e.g. Change X to Y)..."
        : "Ask something about your files...";

    const selectionPreview = highlightedSelection?.selectedText.replace(/\s+/g, " ").trim() ?? "";

    return (
        <div className="input-area-wrapper">
            <div className={`input-container ${isEditMode ? "edit-mode-active" : ""}`}>
                {highlightedSelection && (
                    <div className="input-selection-chip">
                        <div className="input-selection-chip-meta">
                            <span className="input-selection-chip-label">Selected text</span>
                            <span className="input-selection-chip-file">{highlightedSelection.fileName}</span>
                        </div>
                        <div className="input-selection-chip-text">
                            {selectionPreview.length > 140 ? `${selectionPreview.slice(0, 140)}...` : selectionPreview}
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
                        width="20"
                        height="20"
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
                        width="20"
                        height="20"
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
                        : "Edit mode - AI will modify documents | Enter to send"
                    : "Enter to send | Shift+Enter for a new line"}
            </div>
        </div>
    );
}
