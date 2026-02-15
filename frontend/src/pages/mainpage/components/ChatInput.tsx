import { useCallback, useLayoutEffect, useRef, type KeyboardEvent } from "react";

// Type definitions for the ChatInput components props
type ChatInputProps = {
    input: string; // The current words in the chat input field
    isQuerying: boolean; // Whether a query is currently being processed, used to disable input and send button
    isModificationPanelOpen: boolean; // Whether the modification panl
    onInputChange: (value: string) => void;
    onInputKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
    onToggleModificationPanel: () => void;
    onSend: () => void;
};

export default function ChatInput({
    input,
    isQuerying,
    isModificationPanelOpen,
    onInputChange,
    onInputKeyDown,
    onToggleModificationPanel,
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

    return (
        <div className="input-area-wrapper">
            <div className="input-container">
                <textarea
                    ref={textareaRef}
                    className="chat-input"
                    placeholder="Ask something about your files..."
                    rows={1}
                    value={input}
                    onChange={(event) => onInputChange(event.target.value)}
                    onKeyDown={onInputKeyDown}
                />
                <button
                    className={`modification-toggle ${isModificationPanelOpen ? "active" : ""}`}
                    onClick={onToggleModificationPanel}
                    aria-label="Toggle modifications panel"
                    title="Toggle modifications"
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
            <div className="input-hint">Enter to send | Shift+Enter for a new line</div>
        </div>
    );
}
