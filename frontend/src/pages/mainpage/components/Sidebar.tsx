import { useRef, type ChangeEventHandler } from "react";

type SidebarProps = {
    selectedFile: File | null;
    isUploading: boolean;
    onFileSelect: ChangeEventHandler<HTMLInputElement>;
    onUpload: () => void;
    onClearFile: () => void;
};

export default function Sidebar({
    selectedFile,
    isUploading,
    onFileSelect,
    onUpload,
    onClearFile,
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
                <div className="section-title">Files</div>

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
            </div>
        </aside>
    );
}
