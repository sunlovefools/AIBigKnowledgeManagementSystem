import type { DocumentItem } from "../types";

type ModificationPanelProps = {
    documents: DocumentItem[];
    selectedDocId: string | null;
    selectedDocument: DocumentItem | null;
    checkedDocs: Set<string>;
    isLoadingDocs: boolean;
    onRefreshDocuments: () => void;
    onClose: () => void;
    onDocumentSelect: (docId: string) => void;
    onDocumentCheck: (docId: string, checked: boolean) => void;
};

export default function ModificationPanel({
    documents,
    selectedDocId,
    selectedDocument,
    checkedDocs,
    isLoadingDocs,
    onRefreshDocuments,
    onClose,
    onDocumentSelect,
    onDocumentCheck,
}: ModificationPanelProps) {
    return (
        <aside className="modification-panel">
            <div className="mod-panel-header">
                <h3>Modifications</h3>
                <div className="mod-panel-header-actions">
                    <button
                        className="mod-panel-refresh-btn"
                        onClick={onRefreshDocuments}
                        disabled={isLoadingDocs}
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

            <div className="mod-panel-preview-section">
                <h4>Document Preview</h4>
                {isLoadingDocs ? (
                    <div className="mod-panel-loading">Loading documents...</div>
                ) : selectedDocument ? (
                    <div className="mod-panel-preview-content">
                        <div className="preview-doc-info">
                            <strong>{selectedDocument.fileName}</strong>
                            <span className="preview-meta">{selectedDocument.chunks} chunks</span>
                        </div>
                        <div className="preview-text">{selectedDocument.content}</div>
                    </div>
                ) : (
                    <div className="mod-panel-empty">No document selected</div>
                )}
            </div>

            <div className="mod-panel-list-section">
                <h4>Available Documents</h4>
                <div className="mod-panel-file-list">
                    {isLoadingDocs ? (
                        <div className="mod-panel-loading">Loading...</div>
                    ) : documents.length === 0 ? (
                        <div className="mod-panel-empty">No documents available</div>
                    ) : (
                        documents.map((doc) => (
                            <div
                                key={doc.id}
                                className={`mod-panel-file-item ${selectedDocId === doc.id ? "active" : ""}`}
                            >
                                <input
                                    type="checkbox"
                                    checked={checkedDocs.has(doc.id)}
                                    onChange={(event) =>
                                        onDocumentCheck(doc.id, event.target.checked)
                                    }
                                    className="mod-panel-checkbox"
                                />
                                <span
                                    className="mod-panel-file-name"
                                    onClick={() => onDocumentSelect(doc.id)}
                                >
                                    {doc.fileName}
                                </span>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </aside>
    );
}
