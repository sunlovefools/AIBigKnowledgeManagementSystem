import { useRef, type ChangeEventHandler } from "react";
import type { SidebarFileSummary } from "../types";

type SidebarProps = {
    selectedFile: File | null;
    isUploading: boolean;
    files: SidebarFileSummary[];
    isLoadingFiles: boolean;
    fileListError: string | null;
    activeTab: string | null;
    onFileSelect: ChangeEventHandler<HTMLInputElement>;
    onUpload: () => void;
    onClearFile: () => void;
    onOpenFile: (fileName: string) => void;
    onRefreshFiles: () => void;
};

export default function Sidebar({
    selectedFile,
    isUploading,
    files,
    isLoadingFiles,
    fileListError,
    activeTab,
    onFileSelect,
    onUpload,
    onClearFile,
    onOpenFile,
    onRefreshFiles,
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
                        <span className="plus-icon" aria-hidden>
                            +
                        </span>
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
                    <div className="section-title">Knowledge files ({files.length})</div>
                    <button
                        className="sidebar-refresh-btn"
                        onClick={onRefreshFiles}
                        disabled={isLoadingFiles}
                        type="button"
                    >
                        {isLoadingFiles ? "Refreshing..." : "Refresh"}
                    </button>
                </div>

                <div className="sidebar-documents-list">
                    {isLoadingFiles ? (
                        <div className="sidebar-documents-status">Loading files...</div>
                    ) : fileListError ? (
                        <div className="sidebar-documents-status error">{fileListError}</div>
                    ) : files.length === 0 ? (
                        <div className="sidebar-documents-status">No files found in vector database.</div>
                    ) : (
                        files.map((file) => (
                            <button
                                key={file.fileName}
                                className={`sidebar-document-item ${activeTab === file.fileName ? "active" : ""}`}
                                onClick={() => onOpenFile(file.fileName)}
                                type="button"
                            >
                                <div className="sidebar-document-title">{file.fileName}</div>
                                <div className="sidebar-document-preview">{file.preview || "..."}</div>
                            </button>
                        ))
                    )}
                </div>
            </div>
        </aside>
    );
}
