import { useRef, type ChangeEventHandler, type UIEventHandler } from "react";
import type { SidebarFileSummary } from "../types";

type SidebarProps = {
    selectedFile: File | null;
    isUploading: boolean;
    files: SidebarFileSummary[];
    isLoadingFiles: boolean;
    isLoadingMoreFiles: boolean;
    hasMoreFiles: boolean;
    fileListError: string | null;
    activeTab: string | null;
    // Edit mode props
    isEditMode: boolean;
    selectedFileIds: Set<string>;
    onToggleFileSelection: (fileId: string) => void;
    // Handlers
    onFileSelect: ChangeEventHandler<HTMLInputElement>;
    onUpload: () => void;
    onClearFile: () => void;
    onOpenFile: (fileId: string) => void;
    onRefreshFiles: () => void;
    onLoadMoreFiles: () => void;
};

export default function Sidebar({
    selectedFile,
    isUploading,
    files,
    isLoadingFiles,
    isLoadingMoreFiles,
    hasMoreFiles,
    fileListError,
    activeTab,
    isEditMode,
    selectedFileIds,
    onToggleFileSelection,
    onFileSelect,
    onUpload,
    onClearFile,
    onOpenFile,
    onRefreshFiles,
    onLoadMoreFiles,
}: SidebarProps) {
    const fileRef = useRef<HTMLInputElement | null>(null);

    const handleFileSelectClick = () => {
        fileRef.current?.click();
    };

    const handleClear = () => {
        onClearFile();
        if (fileRef.current) {
            fileRef.current.value = "";
        }
    };

    const handleDocumentListScroll: UIEventHandler<HTMLDivElement> = (event) => {
        if (isLoadingFiles || isLoadingMoreFiles || !hasMoreFiles || fileListError) return;
        const target = event.currentTarget;
        if (target.scrollHeight - target.scrollTop - target.clientHeight < 120) {
            onLoadMoreFiles();
        }
    };

    return (
        <aside className="sidebar">
            <div className="sidebar-header">
                <div className="logo-mark">KB</div>
                <div>
                    <div className="eyebrow">Workspace</div>
                    <div className="sidebar-title">Upload sources</div>
                </div>
            </div>
            <p className="sidebar-hint">
                PDF, DOCX or TXT - keep everything you need for the chat here.
            </p>

            <div className="sources-section">
                <div className="section-title">Upload</div>

                <input
                    ref={fileRef}
                    type="file"
                    className="hidden-file-input"
                    style={{ display: "none" }}
                    onChange={onFileSelect}
                    accept=".pdf,.doc,.docx,.txt"
                />

                {!selectedFile && (
                    <button className="add-source-btn" onClick={handleFileSelectClick}>
                        <span className="plus-icon" aria-hidden>+</span>
                        Select file
                    </button>
                )}

                {selectedFile && (
                    <div className="source-card active">
                        <div className="file-info">
                            <span className="file-name">{selectedFile.name}</span>
                        </div>
                        <div className="file-actions">
                            <button
                                className="action-btn upload-confirm-btn"
                                onClick={onUpload}
                                disabled={isUploading}
                            >
                                {isUploading ? "Uploading..." : "Confirm upload"}
                            </button>
                            <button
                                className="action-btn remove-btn"
                                onClick={handleClear}
                                disabled={isUploading}
                            >
                                Remove
                            </button>
                        </div>
                    </div>
                )}

                <div className="sidebar-documents-header">
                    <div className="section-title">
                        Knowledge files ({files.length})
                        {/* Show selection count when in edit mode */}
                        {isEditMode && selectedFileIds.size > 0 && (
                            <span className="sidebar-selection-badge">
                                {selectedFileIds.size} selected
                            </span>
                        )}
                    </div>
                    <button
                        className="sidebar-refresh-btn"
                        onClick={onRefreshFiles}
                        disabled={isLoadingFiles}
                        type="button"
                    >
                        {isLoadingFiles ? "Refreshing..." : "Refresh"}
                    </button>
                </div>

                {/* Hint shown in edit mode */}
                {isEditMode && (
                    <div className="sidebar-edit-mode-hint">
                        ✏️ Check files to scope AI edits, or leave unchecked to search all
                    </div>
                )}

                <div className="sidebar-documents-list" onScroll={handleDocumentListScroll}>
                    {isLoadingFiles ? (
                        <div className="sidebar-documents-status">Loading files...</div>
                    ) : fileListError ? (
                        <div className="sidebar-documents-status error">{fileListError}</div>
                    ) : files.length === 0 ? (
                        <div className="sidebar-documents-status">No files found in vector database.</div>
                    ) : (
                        <>
                            {files.map((file) => (
                                <div
                                    key={file.fileId}
                                    className={`sidebar-document-item-wrapper ${
                                        isEditMode && selectedFileIds.has(file.fileId) ? "selected" : ""
                                    }`}
                                >
                                    {/* Checkbox shown only in edit mode */}
                                    {isEditMode && (
                                        <input
                                            type="checkbox"
                                            className="sidebar-file-checkbox"
                                            checked={selectedFileIds.has(file.fileId)}
                                            onChange={() => onToggleFileSelection(file.fileId)}
                                            aria-label={`Select ${file.fileName} for editing`}
                                            onClick={(e) => e.stopPropagation()}
                                        />
                                    )}
                                    <button
                                        className={`sidebar-document-item ${activeTab === file.fileId ? "active" : ""}`}
                                        onClick={() => onOpenFile(file.fileId)}
                                        type="button"
                                    >
                                        <div className="sidebar-document-title">{file.fileName}</div>
                                        <div className="sidebar-document-preview">
                                            {file.previewTexts || "..."}
                                        </div>
                                    </button>
                                </div>
                            ))}
                            {isLoadingMoreFiles && (
                                <div className="sidebar-documents-status">Loading more files...</div>
                            )}
                        </>
                    )}
                </div>
            </div>
        </aside>
    );
}
