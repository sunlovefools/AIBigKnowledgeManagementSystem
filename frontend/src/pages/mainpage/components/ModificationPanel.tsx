import { useRef } from "react";
import type { FileTabState } from "../types";

// Type definitions for the ModificationPanel component props
type ModificationPanelProps = {
    activeTab: string | null;
    activeTabState: FileTabState | null;
    openTabs: string[];
    isLoadingFiles: boolean;
    onRefreshDocuments: () => void;
    onClose: () => void;
    onTabSelect: (fileName: string) => void;
    onTabClose: (fileName: string) => void;
    onLoadMoreActiveTab: () => void;
};

export default function ModificationPanel({
    activeTab,
    activeTabState,
    openTabs,
    isLoadingFiles,
    onRefreshDocuments,
    onClose,
    onTabSelect,
    onTabClose,
    onLoadMoreActiveTab,
}: ModificationPanelProps) {
    const contentRef = useRef<HTMLDivElement | null>(null);

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
                {!activeTab ? (
                    <div className="mod-panel-empty">No file tab selected.</div>
                ) : activeTabState?.error ? (
                    <div className="mod-panel-empty">{activeTabState.error}</div>
                ) : activeTabState?.chunks.length ? (
                    <>
                        <section className="mod-panel-document-window">
                            <pre className="mod-panel-document-text">{fullDocumentContent}</pre>
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
