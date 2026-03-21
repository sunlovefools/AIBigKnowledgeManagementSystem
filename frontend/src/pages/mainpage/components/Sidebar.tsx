import { useRef, useState, useEffect, type ChangeEventHandler, type KeyboardEvent } from "react";
import type { SidebarFileSummary } from "../types";

// ── Client-side file-name validation ──────────────────────────────────────────
// Mirrors the backend rules so the user gets instant feedback without a round-trip.
const ILLEGAL_CHARS = /[/\\:*?"<>|\x00]/;
const RESERVED_NAMES = new Set([
    "CON","PRN","AUX","NUL",
    "COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
    "LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9",
]);
const MAX_NAME_LENGTH = 200;

/**
 * Returns an error string when the name is invalid, or null when it is fine.
 * @param name - the candidate file name
 * @param existingFiles - current sidebar file list for duplicate detection
 * @param excludeFileId - skip this fileId when checking duplicates (used for rename)
 */
function validateFileName(
    name: string,
    existingFiles: SidebarFileSummary[],
    excludeFileId?: string
): string | null {
    const trimmed = name.trim();

    if (!trimmed) return "File name must not be empty.";

    if (trimmed.length > MAX_NAME_LENGTH)
        return `File name must not exceed ${MAX_NAME_LENGTH} characters.`;

    if (ILLEGAL_CHARS.test(trimmed))
        return 'File name must not contain: / \\ : * ? " < > |';

    // Control characters
    if ([...trimmed].some((ch) => ch.charCodeAt(0) < 32))
        return "File name must not contain control characters.";

    // Pure dots
    if (/^\.+$/.test(trimmed))
        return "File name must not consist solely of dots.";

    // Trailing dot or space
    if (trimmed !== trimmed.replace(/[. ]+$/, ""))
        return "File name must not end with a dot or space.";

    // Windows reserved names (check base name before first dot)
    const base = trimmed.split(".")[0].toUpperCase();
    if (RESERVED_NAMES.has(base))
        return `"${base}" is a reserved system name and cannot be used.`;

    // Duplicate check (case-insensitive)
    const lower = trimmed.toLowerCase();
    const duplicate = existingFiles.find(
        (f) => f.fileName.trim().toLowerCase() === lower && f.fileId !== excludeFileId
    );
    if (duplicate)
        return `A file named "${duplicate.fileName}" already exists. Please choose a different name.`;

    return null;
}

type SidebarProps = {
    selectedFile: File | null;
    isUploading: boolean;
    files: SidebarFileSummary[];
    isLoadingFiles: boolean;
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
    // New file / rename
    onCreateBlankFile: (fileName: string) => Promise<{ ok: boolean; fileId?: string; error?: string }>;
    onRenameFile: (fileId: string, newName: string) => Promise<{ ok: boolean; error?: string }>;
};

export default function Sidebar({
    selectedFile,
    isUploading,
    files,
    isLoadingFiles,
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
    onCreateBlankFile,
    onRenameFile,
}: SidebarProps) {
    const fileRef = useRef<HTMLInputElement | null>(null);

    // ── New-file creation state ──────────────────────────────────────
    const [isCreatingFile, setIsCreatingFile] = useState(false);
    const [newFileName, setNewFileName] = useState("");
    const [createError, setCreateError] = useState<string | null>(null);
    const [isSubmittingCreate, setIsSubmittingCreate] = useState(false);
    const newFileInputRef = useRef<HTMLInputElement | null>(null);

    // ── Inline rename state ──────────────────────────────────────────
    const [renamingFileId, setRenamingFileId] = useState<string | null>(null);
    const [renameValue, setRenameValue] = useState("");
    const [renameError, setRenameError] = useState<string | null>(null);
    const [isSubmittingRename, setIsSubmittingRename] = useState(false);
    const renameInputRef = useRef<HTMLInputElement | null>(null);

    // Focus the new-file input whenever it becomes visible
    useEffect(() => {
        if (isCreatingFile) {
            setTimeout(() => newFileInputRef.current?.focus(), 0);
        }
    }, [isCreatingFile]);

    // Focus the rename input whenever a file enters rename mode
    useEffect(() => {
        if (renamingFileId) {
            setTimeout(() => renameInputRef.current?.focus(), 0);
        }
    }, [renamingFileId]);

    // ── Upload helpers ───────────────────────────────────────────────
    const handleFileSelectClick = () => fileRef.current?.click();

    const handleClear = () => {
        onClearFile();
        if (fileRef.current) fileRef.current.value = "";
    };

    // ── New-file handlers ────────────────────────────────────────────
    const openCreateForm = () => {
        setNewFileName("");
        setCreateError(null);
        setIsCreatingFile(true);
    };

    const cancelCreate = () => {
        setIsCreatingFile(false);
        setNewFileName("");
        setCreateError(null);
    };

    const submitCreate = async () => {
        const trimmed = newFileName.trim();
        const validationError = validateFileName(trimmed, files);
        if (validationError) {
            setCreateError(validationError);
            newFileInputRef.current?.focus();
            return;
        }
        setIsSubmittingCreate(true);
        setCreateError(null);
        const result = await onCreateBlankFile(trimmed);
        setIsSubmittingCreate(false);
        if (!result.ok) {
            setCreateError(result.error ?? "Failed to create file.");
            newFileInputRef.current?.focus();
            return;
        }
        setIsCreatingFile(false);
        setNewFileName("");
        if (result.fileId) {
            onOpenFile(result.fileId);
        }
    };

    const handleCreateKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") {
            e.preventDefault();
            void submitCreate();
        } else if (e.key === "Escape") {
            cancelCreate();
        }
    };

    // ── Rename handlers ──────────────────────────────────────────────
    const startRename = (fileId: string, currentName: string) => {
        setRenamingFileId(fileId);
        setRenameValue(currentName);
        setRenameError(null);
    };

    const cancelRename = () => {
        setRenamingFileId(null);
        setRenameValue("");
        setRenameError(null);
    };

    const submitRename = async (fileId: string) => {
        const trimmed = renameValue.trim();
        const originalName = files.find((f) => f.fileId === fileId)?.fileName ?? "";
        if (trimmed === originalName) {
            // No change — just close without a network request
            cancelRename();
            return;
        }
        const validationError = validateFileName(trimmed, files, fileId);
        if (validationError) {
            setRenameError(validationError);
            renameInputRef.current?.focus();
            return;
        }
        setIsSubmittingRename(true);
        setRenameError(null);
        const result = await onRenameFile(fileId, trimmed);
        setIsSubmittingRename(false);
        if (!result.ok) {
            setRenameError(result.error ?? "Failed to rename file.");
            renameInputRef.current?.focus();
            return;
        }
        cancelRename();
    };

    const handleRenameKeyDown = (e: KeyboardEvent<HTMLInputElement>, fileId: string) => {
        if (e.key === "Enter") {
            e.preventDefault();
            void submitRename(fileId);
        } else if (e.key === "Escape") {
            cancelRename();
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

                {/* ── Knowledge files header ── */}
                <div className="sidebar-documents-header">
                    <div className="section-title">
                        Knowledge files ({files.length})
                        {isEditMode && selectedFileIds.size > 0 && (
                            <span className="sidebar-selection-badge">
                                {selectedFileIds.size} selected
                            </span>
                        )}
                    </div>
                    <div className="sidebar-header-actions">
                        <button
                            className="sidebar-new-file-btn"
                            onClick={openCreateForm}
                            disabled={isCreatingFile || isLoadingFiles}
                            type="button"
                            title="Create a new blank file"
                            aria-label="Create new blank file"
                        >
                            + New
                        </button>
                        <button
                            className="sidebar-refresh-btn"
                            onClick={onRefreshFiles}
                            disabled={isLoadingFiles}
                            type="button"
                        >
                            {isLoadingFiles ? "Refreshing..." : "Refresh"}
                        </button>
                    </div>
                </div>

                {/* ── Edit-mode hint ── */}
                {isEditMode && (
                    <div className="sidebar-edit-mode-hint">
                        ✏️ Check files to scope AI edits, or leave unchecked to search all
                    </div>
                )}

                {/* ── Inline new-file creation form ── */}
                {isCreatingFile && (
                    <div className="sidebar-create-file-form">
                        <input
                            ref={newFileInputRef}
                            className="sidebar-rename-input"
                            type="text"
                            value={newFileName}
                            onChange={(e) => {
                                setNewFileName(e.target.value);
                                // Clear error as soon as the current value becomes valid
                                if (createError) {
                                    const err = validateFileName(e.target.value.trim(), files);
                                    if (!err) setCreateError(null);
                                }
                            }}
                            onKeyDown={handleCreateKeyDown}
                            placeholder="File name…"
                            disabled={isSubmittingCreate}
                            maxLength={MAX_NAME_LENGTH}
                            aria-label="New file name"
                        />
                        {createError && (
                            <div className="sidebar-rename-error">{createError}</div>
                        )}
                        <div className="sidebar-rename-actions">
                            <button
                                className="sidebar-rename-confirm-btn"
                                type="button"
                                onClick={() => { void submitCreate(); }}
                                disabled={isSubmittingCreate}
                            >
                                {isSubmittingCreate ? "Creating…" : "Create"}
                            </button>
                            <button
                                className="sidebar-rename-cancel-btn"
                                type="button"
                                onClick={cancelCreate}
                                disabled={isSubmittingCreate}
                            >
                                Cancel
                            </button>
                        </div>
                    </div>
                )}

                {/* ── File list ── */}
                <div className="sidebar-documents-list">
                    {isLoadingFiles ? (
                        <div className="sidebar-documents-status">Loading files...</div>
                    ) : fileListError ? (
                        <div className="sidebar-documents-status error">{fileListError}</div>
                    ) : files.length === 0 ? (
                        <div className="sidebar-documents-status">No files found in vector database.</div>
                    ) : (
                        files.map((file) => (
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

                                {/* ── Inline rename view ── */}
                                {renamingFileId === file.fileId ? (
                                    <div className="sidebar-rename-wrapper">
                                        <input
                                            ref={renameInputRef}
                                            className="sidebar-rename-input"
                                            type="text"
                                            value={renameValue}
                                            onChange={(e) => setRenameValue(e.target.value)}
                                            onKeyDown={(e) => handleRenameKeyDown(e, file.fileId)}
                                            disabled={isSubmittingRename}
                                            maxLength={200}
                                            aria-label={`Rename ${file.fileName}`}
                                        />
                                        {renameError && (
                                            <div className="sidebar-rename-error">{renameError}</div>
                                        )}
                                        <div className="sidebar-rename-actions">
                                            <button
                                                className="sidebar-rename-confirm-btn"
                                                type="button"
                                                onClick={() => { void submitRename(file.fileId); }}
                                                disabled={isSubmittingRename}
                                            >
                                                {isSubmittingRename ? "Saving…" : "Save"}
                                            </button>
                                            <button
                                                className="sidebar-rename-cancel-btn"
                                                type="button"
                                                onClick={cancelRename}
                                                disabled={isSubmittingRename}
                                            >
                                                Cancel
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    /* ── Normal file row ── */
                                    <div className="sidebar-document-row">
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
                                        <button
                                            className="sidebar-rename-icon-btn"
                                            type="button"
                                            title={`Rename "${file.fileName}"`}
                                            aria-label={`Rename ${file.fileName}`}
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                startRename(file.fileId, file.fileName);
                                            }}
                                        >
                                            {/* Pencil SVG */}
                                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                                                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                                            </svg>
                                        </button>
                                    </div>
                                )}
                            </div>
                        ))
                    )}
                </div>
            </div>
        </aside>
    );
}
