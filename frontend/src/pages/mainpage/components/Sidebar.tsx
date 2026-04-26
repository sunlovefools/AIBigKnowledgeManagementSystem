import { useRef, useState, useEffect, type ChangeEventHandler, type KeyboardEvent } from "react";
import type { SidebarFileSummary, UserCollectionSummary } from "../types";
import { FILE_INPUT_ACCEPT } from "../utils/uploadFormats";

// ── Client-side file-name validation ──────────────────────────────────────────
// Mirrors the backend rules so the user gets instant feedback without a round-trip.
const ILLEGAL_CHARS = /[/\\:*?"<>|]/;
const RESERVED_NAMES = new Set([
    "CON","PRN","AUX","NUL",
    "COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
    "LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9",
]);
const MAX_NAME_LENGTH = 200;
const MAX_COLLECTION_NAME_LENGTH = 120;

// Extensions that imply a binary format.
// New blank files are always plain text — these extensions would be misleading.
const BINARY_EXTENSIONS = new Set([
    "pdf","doc","docx","xls","xlsx","ppt","pptx",
    "odt","ods","odp","rtf","pages","numbers","key",
    "zip","rar","7z","tar","gz",
    "png","jpg","jpeg","gif","webp","svg","bmp","tiff",
    "mp3","mp4","wav","avi","mov","mkv",
    "exe","dll","bin","iso",
]);

/**
 * Returns an error string when the name is invalid, or null when it is fine.
 * @param name - the candidate file name
 * @param existingFiles - current sidebar file list for duplicate detection
 * @param excludeFileId - skip this fileId when checking duplicates (used for rename)
 * @param isCreate - when true, also blocks binary-format extensions
 */
function validateFileName(
    name: string,
    existingFiles: SidebarFileSummary[],
    excludeFileId?: string,
    isCreate?: boolean
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

    // Binary extension check — only for new file creation, not rename
    if (isCreate) {
        const ext = trimmed.includes(".")
            ? trimmed.split(".").pop()!.toLowerCase()
            : "";
        if (ext && BINARY_EXTENSIONS.has(ext)) {
            return `".${ext}" files cannot be created as blank text files. `
                + `Use the Upload button to add a real ${ext.toUpperCase()} file, `
                + `or create a plain text file without that extension (e.g. "${trimmed.replace(/\.[^.]+$/, "")}" or "${trimmed.replace(/\.[^.]+$/, "")}.txt").`;
        }
    }

    // Duplicate check (case-insensitive)
    const lower = trimmed.toLowerCase();
    const duplicate = existingFiles.find(
        (f) => f.fileName.trim().toLowerCase() === lower && f.fileId !== excludeFileId
    );
    if (duplicate)
        return `A file named "${duplicate.fileName}" already exists. Please choose a different name.`;

    return null;
}

function validateCollectionName(
    name: string,
    existingCollections: UserCollectionSummary[],
    excludeCollectionId?: string
): string | null {
    const trimmed = name.trim();
    if (!trimmed) return "Collection name must not be empty.";
    if (trimmed.length > MAX_COLLECTION_NAME_LENGTH) {
        return `Collection name must not exceed ${MAX_COLLECTION_NAME_LENGTH} characters.`;
    }

    const duplicate = existingCollections.find(
        (collection) =>
            collection.name.trim().toLowerCase() === trimmed.toLowerCase() &&
            collection.collectionId !== excludeCollectionId
    );
    if (duplicate) {
        return `A collection named "${duplicate.name}" already exists.`;
    }

    return null;
}

type SidebarProps = {
    collections: UserCollectionSummary[];
    activeCollectionId: string | null;
    isLoadingCollections: boolean;
    collectionError: string | null;
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
    onCollapseSources?: () => void;
    // Handlers
    onFileSelect: ChangeEventHandler<HTMLInputElement>;
    onUpload: () => void;
    onClearFile: () => void;
    onOpenFile: (fileId: string) => void;
    onRefreshFiles: () => void;
    // New file / rename
    onCreateBlankFile: (fileName: string) => Promise<{ ok: boolean; fileId?: string; error?: string }>;
    onRenameFile: (fileId: string, newName: string) => Promise<{ ok: boolean; error?: string }>;
    onRequestDeleteFile: (fileId: string) => void;
    // Collection CRUD
    onSelectCollection: (collectionId: string) => void;
    onCreateCollection: (name: string) => Promise<{ ok: boolean; collectionId?: string; error?: string }>;
    onRenameCollection: (collectionId: string, newName: string) => Promise<{ ok: boolean; error?: string }>;
    onDeleteCollection: (collectionId: string) => Promise<{ ok: boolean; warningText?: string; error?: string }>;
    // Optimistic creation: IDs still being committed to the DB
    pendingCreationFileIds: Set<string>;
    pendingSaveFileIds: Set<string>;
};

export default function Sidebar({
    collections,
    activeCollectionId,
    isLoadingCollections,
    collectionError,
    selectedFile,
    isUploading,
    files,
    isLoadingFiles,
    fileListError,
    activeTab,
    isEditMode,
    selectedFileIds,
    onToggleFileSelection,
    onCollapseSources,
    onFileSelect,
    onUpload,
    onClearFile,
    onOpenFile,
    onRefreshFiles,
    onCreateBlankFile,
    onRenameFile,
    onRequestDeleteFile,
    onSelectCollection,
    onCreateCollection,
    onRenameCollection,
    onDeleteCollection,
    pendingCreationFileIds,
    pendingSaveFileIds,
}: SidebarProps) {
    const fileRef = useRef<HTMLInputElement | null>(null);
    const collectionSwitcherRef = useRef<HTMLDivElement | null>(null);
    const collectionActionMenuRef = useRef<HTMLDivElement | null>(null);
    const activeCollection = collections.find((collection) => collection.collectionId === activeCollectionId) ?? null;

    const [isCollectionSwitcherOpen, setIsCollectionSwitcherOpen] = useState(false);
    const [collectionFilter, setCollectionFilter] = useState("");
    const [isCollectionActionMenuOpen, setIsCollectionActionMenuOpen] = useState(false);
    const [isCreatingCollection, setIsCreatingCollection] = useState(false);
    const [newCollectionName, setNewCollectionName] = useState("");
    const [isSubmittingCollectionCreate, setIsSubmittingCollectionCreate] = useState(false);
    const [isRenamingCollection, setIsRenamingCollection] = useState(false);
    const [collectionRenameValue, setCollectionRenameValue] = useState("");
    const [isSubmittingCollectionRename, setIsSubmittingCollectionRename] = useState(false);
    const [isDeletingCollection, setIsDeletingCollection] = useState(false);
    const [collectionActionError, setCollectionActionError] = useState<string | null>(null);
    const [collectionActionInfo, setCollectionActionInfo] = useState<string | null>(null);
    const collectionCreateInputRef = useRef<HTMLInputElement | null>(null);
    const collectionRenameInputRef = useRef<HTMLInputElement | null>(null);

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
    const [openFileActionMenuId, setOpenFileActionMenuId] = useState<string | null>(null);
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

    useEffect(() => {
        if (isCreatingCollection) {
            setTimeout(() => collectionCreateInputRef.current?.focus(), 0);
        }
    }, [isCreatingCollection]);

    useEffect(() => {
        if (isRenamingCollection) {
            setTimeout(() => collectionRenameInputRef.current?.focus(), 0);
        }
    }, [isRenamingCollection]);

    useEffect(() => {
        setIsRenamingCollection(false);
        setCollectionRenameValue("");
        setCollectionActionError(null);
        setCollectionActionInfo(null);
    }, [activeCollectionId]);

    useEffect(() => {
        if (openFileActionMenuId && !files.some((file) => file.fileId === openFileActionMenuId)) {
            setOpenFileActionMenuId(null);
        }
    }, [files, openFileActionMenuId]);

    useEffect(() => {
        if (!isCollectionSwitcherOpen && !isCollectionActionMenuOpen && !openFileActionMenuId) return;

        const handlePointerDown = (event: MouseEvent) => {
            const target = event.target as HTMLElement | null;
            if (!target) return;
            if (collectionSwitcherRef.current?.contains(target)) return;
            if (collectionActionMenuRef.current?.contains(target)) return;
            if (target.closest(".sidebar-document-actions")) return;
            setIsCollectionSwitcherOpen(false);
            setIsCollectionActionMenuOpen(false);
            setOpenFileActionMenuId(null);
        };

        const handleEscape = (event: globalThis.KeyboardEvent) => {
            if (event.key !== "Escape") return;
            setIsCollectionSwitcherOpen(false);
            setIsCollectionActionMenuOpen(false);
            setOpenFileActionMenuId(null);
        };

        window.addEventListener("mousedown", handlePointerDown);
        window.addEventListener("keydown", handleEscape);
        return () => {
            window.removeEventListener("mousedown", handlePointerDown);
            window.removeEventListener("keydown", handleEscape);
        };
    }, [isCollectionActionMenuOpen, isCollectionSwitcherOpen, openFileActionMenuId]);

    useEffect(() => {
        if (!openFileActionMenuId) return;

        const handleEscape = (event: globalThis.KeyboardEvent) => {
            if (event.key === "Escape") {
                setOpenFileActionMenuId(null);
            }
        };

        window.addEventListener("keydown", handleEscape);
        return () => window.removeEventListener("keydown", handleEscape);
    }, [openFileActionMenuId]);

    // ── Upload helpers ───────────────────────────────────────────────
    const handleFileSelectClick = () => fileRef.current?.click();

    const handleClear = () => {
        onClearFile();
        if (fileRef.current) fileRef.current.value = "";
    };

    const openCreateCollectionForm = () => {
        setIsCollectionSwitcherOpen(false);
        setIsCollectionActionMenuOpen(false);
        setIsCreatingCollection(true);
        setNewCollectionName("");
        setCollectionActionError(null);
        setCollectionActionInfo(null);
    };

    const cancelCreateCollection = () => {
        setIsCreatingCollection(false);
        setNewCollectionName("");
        setCollectionActionError(null);
    };

    const submitCreateCollection = async () => {
        const validationError = validateCollectionName(newCollectionName, collections);
        if (validationError) {
            setCollectionActionError(validationError);
            collectionCreateInputRef.current?.focus();
            return;
        }

        setIsSubmittingCollectionCreate(true);
        setCollectionActionError(null);
        setCollectionActionInfo(null);
        const result = await onCreateCollection(newCollectionName.trim());
        setIsSubmittingCollectionCreate(false);

        if (!result.ok || !result.collectionId) {
            setCollectionActionError(result.error ?? "Failed to create collection.");
            collectionCreateInputRef.current?.focus();
            return;
        }

        setIsCreatingCollection(false);
        setNewCollectionName("");
        onSelectCollection(result.collectionId);
        setCollectionActionInfo("Collection created.");
    };

    const startRenameCollection = () => {
        if (!activeCollection) return;
        setIsCollectionActionMenuOpen(false);
        setIsRenamingCollection(true);
        setCollectionRenameValue(activeCollection.name);
        setCollectionActionError(null);
        setCollectionActionInfo(null);
    };

    const cancelRenameCollection = () => {
        setIsRenamingCollection(false);
        setCollectionRenameValue("");
        setCollectionActionError(null);
    };

    const submitRenameCollection = async () => {
        if (!activeCollection) return;
        const validationError = validateCollectionName(
            collectionRenameValue,
            collections,
            activeCollection.collectionId
        );
        if (validationError) {
            setCollectionActionError(validationError);
            collectionRenameInputRef.current?.focus();
            return;
        }

        const trimmed = collectionRenameValue.trim();
        if (trimmed === activeCollection.name) {
            cancelRenameCollection();
            return;
        }

        setIsSubmittingCollectionRename(true);
        setCollectionActionError(null);
        setCollectionActionInfo(null);
        const result = await onRenameCollection(activeCollection.collectionId, trimmed);
        setIsSubmittingCollectionRename(false);

        if (!result.ok) {
            setCollectionActionError(result.error ?? "Failed to rename collection.");
            collectionRenameInputRef.current?.focus();
            return;
        }

        setIsRenamingCollection(false);
        setCollectionRenameValue("");
        setCollectionActionInfo("Collection renamed.");
    };

    const handleDeleteCollection = async () => {
        if (!activeCollection || activeCollection.isDefault || isDeletingCollection) return;
        setIsCollectionActionMenuOpen(false);

        const confirmed = window.confirm(
            `Delete collection "${activeCollection.name}"?\n\n` +
            "This will permanently delete all files and indexed chunks in this collection."
        );
        if (!confirmed) return;

        setIsDeletingCollection(true);
        setCollectionActionError(null);
        setCollectionActionInfo(null);
        const result = await onDeleteCollection(activeCollection.collectionId);
        setIsDeletingCollection(false);

        if (!result.ok) {
            setCollectionActionError(result.error ?? "Failed to delete collection.");
            return;
        }

        setCollectionActionInfo(`Collection deleted.${result.warningText ?? ""}`.trim());
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
        const validationError = validateFileName(trimmed, files, undefined, true);
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

    const handleCreateCollectionKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") {
            e.preventDefault();
            void submitCreateCollection();
        } else if (e.key === "Escape") {
            cancelCreateCollection();
        }
    };

    const handleRenameCollectionKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") {
            e.preventDefault();
            void submitRenameCollection();
        } else if (e.key === "Escape") {
            cancelRenameCollection();
        }
    };

    // ── Rename handlers ──────────────────────────────────────────────
    const startRename = (fileId: string, currentName: string) => {
        setRenamingFileId(fileId);
        setRenameValue(currentName);
        setRenameError(null);
        setOpenFileActionMenuId(null);
    };

    const cancelRename = () => {
        setRenamingFileId(null);
        setRenameValue("");
        setRenameError(null);
    };

    const submitRename = (fileId: string) => {
        const trimmed = renameValue.trim();
        const originalName = files.find((f) => f.fileId === fileId)?.fileName ?? "";
        if (trimmed === originalName) {
            cancelRename();
            return;
        }
        const validationError = validateFileName(trimmed, files, fileId);
        if (validationError) {
            setRenameError(validationError);
            renameInputRef.current?.focus();
            return;
        }

        // ── Close the UI immediately — optimistic ──────────────────────────
        // onRenameFile already patches local state first, so the sidebar name
        // changes at once. We fire the DB write in the background and only
        // surface an error in the chat area if it fails.
        cancelRename();
        void onRenameFile(fileId, trimmed);
    };

    const handleRenameKeyDown = (e: KeyboardEvent<HTMLInputElement>, fileId: string) => {
        if (e.key === "Enter") {
            e.preventDefault();
            submitRename(fileId);
        } else if (e.key === "Escape") {
            cancelRename();
        }
    };

    const filteredCollections = collectionFilter.trim()
        ? collections.filter((collection) =>
            collection.name.toLowerCase().includes(collectionFilter.trim().toLowerCase())
        )
        : collections;

    return (
        <aside className="sidebar" onClick={() => setOpenFileActionMenuId(null)}>
            <div className="sidebar-header">
                <div className="sidebar-header-main">
                    <div className="logo-mark">KB</div>
                    <div>
                        <div className="eyebrow">Workspace</div>
                        <div className="sidebar-title">Sources</div>
                    </div>
                </div>
                {onCollapseSources && (
                    <button
                        className="sidebar-collapse-btn"
                        type="button"
                        onClick={onCollapseSources}
                        aria-label="Collapse sources"
                        title="Collapse sources"
                    >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <polyline points="15 18 9 12 15 6" />
                        </svg>
                    </button>
                )}
            </div>

            <div className="sources-section">
                <div className="collection-section">
                    <div className="section-title">Sources browser</div>
                    {isLoadingCollections ? (
                        <div className="sidebar-documents-status">Loading collections...</div>
                    ) : collectionError ? (
                        <div className="sidebar-documents-status error">{collectionError}</div>
                    ) : collections.length === 0 ? (
                        <div className="sidebar-documents-status">No collections available.</div>
                    ) : (
                        <>
                            <div className="collection-browser-header">
                                <div className="collection-switcher" ref={collectionSwitcherRef}>
                                    <button
                                        className={`collection-switcher-trigger ${isCollectionSwitcherOpen ? "open" : ""}`}
                                        type="button"
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            setIsCollectionSwitcherOpen((current) => !current);
                                            setIsCollectionActionMenuOpen(false);
                                            setOpenFileActionMenuId(null);
                                        }}
                                        aria-haspopup="dialog"
                                        aria-expanded={isCollectionSwitcherOpen}
                                    >
                                        <span className="collection-switcher-label">
                                            {activeCollection?.name ?? "Choose collection"}
                                        </span>
                                        <span className="collection-switcher-meta">
                                            {activeCollection ? `${activeCollection.fileCount} file(s)` : "No source selected"}
                                        </span>
                                        <span className="collection-switcher-chevron" aria-hidden="true">
                                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                                                <polyline points="6 9 12 15 18 9" />
                                            </svg>
                                        </span>
                                    </button>

                                    {isCollectionSwitcherOpen && (
                                        <div className="collection-switcher-popover" role="dialog" aria-label="Switch source collection">
                                            <input
                                                className="collection-switcher-search"
                                                type="text"
                                                value={collectionFilter}
                                                onChange={(event) => setCollectionFilter(event.target.value)}
                                                placeholder="Find collection..."
                                            />
                                            <div className="collection-switcher-list">
                                                {filteredCollections.length === 0 ? (
                                                    <div className="collection-switcher-status">No matching collections.</div>
                                                ) : (
                                                    filteredCollections.map((collection) => (
                                                        <button
                                                            key={collection.collectionId}
                                                            className={`collection-switcher-row ${collection.collectionId === activeCollectionId ? "active" : ""}`}
                                                            type="button"
                                                            onClick={() => {
                                                                onSelectCollection(collection.collectionId);
                                                                setIsCollectionSwitcherOpen(false);
                                                            }}
                                                        >
                                                            <span className="collection-switcher-row-title">{collection.name}</span>
                                                            <span className="collection-switcher-row-meta">{collection.fileCount} file(s)</span>
                                                        </button>
                                                    ))
                                                )}
                                            </div>
                                            <button
                                                className="collection-switcher-new"
                                                type="button"
                                                onClick={openCreateCollectionForm}
                                                disabled={isCreatingCollection || isSubmittingCollectionCreate}
                                            >
                                                <span aria-hidden="true">+</span>
                                                New collection
                                            </button>
                                        </div>
                                    )}
                                </div>

                                <div className="collection-action-menu" ref={collectionActionMenuRef}>
                                    <button
                                        className={`collection-action-menu-trigger ${isCollectionActionMenuOpen ? "open" : ""}`}
                                        type="button"
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            setIsCollectionActionMenuOpen((current) => !current);
                                            setIsCollectionSwitcherOpen(false);
                                            setOpenFileActionMenuId(null);
                                        }}
                                        aria-haspopup="menu"
                                        aria-expanded={isCollectionActionMenuOpen}
                                        aria-label="Collection actions"
                                    >
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                                            <circle cx="12" cy="5" r="1.9" />
                                            <circle cx="12" cy="12" r="1.9" />
                                            <circle cx="12" cy="19" r="1.9" />
                                        </svg>
                                    </button>

                                    {isCollectionActionMenuOpen && (
                                        <div className="collection-action-dropdown" role="menu">
                                            <button
                                                className="collection-action-dropdown-item"
                                                type="button"
                                                role="menuitem"
                                                onClick={openCreateCollectionForm}
                                                disabled={isCreatingCollection || isSubmittingCollectionCreate}
                                            >
                                                New collection
                                            </button>
                                            <button
                                                className="collection-action-dropdown-item"
                                                type="button"
                                                role="menuitem"
                                                onClick={startRenameCollection}
                                                disabled={!activeCollection || isRenamingCollection || isSubmittingCollectionRename}
                                            >
                                                Rename
                                            </button>
                                            <button
                                                className="collection-action-dropdown-item danger"
                                                type="button"
                                                role="menuitem"
                                                onClick={() => { void handleDeleteCollection(); }}
                                                disabled={!activeCollection || activeCollection.isDefault || isDeletingCollection}
                                                title={activeCollection?.isDefault ? "Default collection cannot be deleted" : "Delete collection and all files in it"}
                                            >
                                                {isDeletingCollection ? "Deleting..." : "Delete"}
                                            </button>
                                        </div>
                                    )}
                                </div>
                            </div>
                        </>
                    )}

                    {isCreatingCollection && (
                        <div className="sidebar-create-file-form">
                            <input
                                ref={collectionCreateInputRef}
                                className="sidebar-rename-input"
                                type="text"
                                value={newCollectionName}
                                onChange={(event) => setNewCollectionName(event.target.value)}
                                onKeyDown={handleCreateCollectionKeyDown}
                                placeholder="New collection name"
                                disabled={isSubmittingCollectionCreate}
                                maxLength={MAX_COLLECTION_NAME_LENGTH}
                            />
                            <div className="sidebar-rename-actions">
                                <button
                                    className="sidebar-rename-confirm-btn"
                                    type="button"
                                    onClick={() => { void submitCreateCollection(); }}
                                    disabled={isSubmittingCollectionCreate}
                                >
                                    {isSubmittingCollectionCreate ? "Creating..." : "Create"}
                                </button>
                                <button
                                    className="sidebar-rename-cancel-btn"
                                    type="button"
                                    onClick={cancelCreateCollection}
                                    disabled={isSubmittingCollectionCreate}
                                >
                                    Cancel
                                </button>
                            </div>
                        </div>
                    )}

                    {isRenamingCollection && activeCollection && (
                        <div className="sidebar-create-file-form">
                            <input
                                ref={collectionRenameInputRef}
                                className="sidebar-rename-input"
                                type="text"
                                value={collectionRenameValue}
                                onChange={(event) => setCollectionRenameValue(event.target.value)}
                                onKeyDown={handleRenameCollectionKeyDown}
                                disabled={isSubmittingCollectionRename}
                                maxLength={MAX_COLLECTION_NAME_LENGTH}
                            />
                            <div className="sidebar-rename-actions">
                                <button
                                    className="sidebar-rename-confirm-btn"
                                    type="button"
                                    onClick={() => { void submitRenameCollection(); }}
                                    disabled={isSubmittingCollectionRename}
                                >
                                    {isSubmittingCollectionRename ? "Saving..." : "Save"}
                                </button>
                                <button
                                    className="sidebar-rename-cancel-btn"
                                    type="button"
                                    onClick={cancelRenameCollection}
                                    disabled={isSubmittingCollectionRename}
                                >
                                    Cancel
                                </button>
                            </div>
                        </div>
                    )}

                    {collectionActionError && (
                        <div className="sidebar-rename-error">{collectionActionError}</div>
                    )}
                    {collectionActionInfo && (
                        <div className="sidebar-collection-info">{collectionActionInfo}</div>
                    )}
                </div>

                <input
                    ref={fileRef}
                    type="file"
                    className="hidden-file-input"
                    style={{ display: "none" }}
                    onChange={onFileSelect}
                    accept={FILE_INPUT_ACCEPT}
                />

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
                                {isUploading ? "Uploading..." : "Upload"}
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
                        Sources ({files.length})
                        {isEditMode && selectedFileIds.size > 0 && (
                            <span className="sidebar-selection-badge">
                                {selectedFileIds.size} selected
                            </span>
                        )}
                    </div>
                    <div className="sidebar-header-actions">
                        <button
                            className="sidebar-upload-btn"
                            onClick={handleFileSelectClick}
                            disabled={isUploading}
                            type="button"
                            title={selectedFile ? "Choose a different file" : "Upload a file"}
                            aria-label={selectedFile ? "Choose a different file" : "Upload file"}
                        >
                            Upload
                        </button>
                        <button
                            className="sidebar-new-file-btn"
                            onClick={openCreateForm}
                            disabled={isCreatingFile || isLoadingFiles}
                            type="button"
                            title="Create a new blank file"
                            aria-label="Create new blank file"
                        >
                            New note
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
                        Check files to scope AI edits, or leave unchecked to search all sources.
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
                                    const err = validateFileName(e.target.value.trim(), files, undefined, true);
                                    if (!err) setCreateError(null);
                                }
                            }}
                            onKeyDown={handleCreateKeyDown}
                            placeholder="e.g. meeting-notes or notes.txt"
                            disabled={isSubmittingCreate}
                            maxLength={MAX_NAME_LENGTH}
                            aria-label="New file name"
                        />
                        <div className="sidebar-create-hint">
                            Plain text / Markdown only. To add a PDF or Word doc, use the Upload button above.
                        </div>
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
                        files.map((file) => {
                            const isFilePending = pendingCreationFileIds.has(file.fileId) || pendingSaveFileIds.has(file.fileId);
                            return (
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
                                            maxLength={MAX_NAME_LENGTH}
                                            aria-label={`Rename ${file.fileName}`}
                                            disabled={isFilePending}
                                        />
                                        {renameError && (
                                            <div className="sidebar-rename-error">{renameError}</div>
                                        )}
                                        <div className="sidebar-rename-actions">
                                            <button
                                                className="sidebar-rename-confirm-btn"
                                                type="button"
                                                onClick={() => submitRename(file.fileId)}
                                                disabled={isFilePending}
                                            >
                                                Save
                                            </button>
                                            <button
                                                className="sidebar-rename-cancel-btn"
                                                type="button"
                                                onClick={cancelRename}
                                            >
                                                Cancel
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    /* ── Normal file row ── */
                                    <div className={`sidebar-document-row ${activeTab === file.fileId ? "active" : ""}`}>
                                        <button
                                            className={`sidebar-document-item ${activeTab === file.fileId ? "active" : ""}`}
                                            onClick={() => {
                                                setOpenFileActionMenuId(null);
                                                onOpenFile(file.fileId);
                                            }}
                                            type="button"
                                        >
                                            <div className="sidebar-document-title">
                                                {file.fileName}
                                                {isFilePending && (
                                                    <span className="sidebar-creating-badge" aria-label="Saving to database">
                                                        saving…
                                                    </span>
                                                )}
                                            </div>
                                            <div className="sidebar-document-preview">
                                                {file.previewTexts || "..."}
                                            </div>
                                        </button>
                                        <div
                                            className="sidebar-document-actions"
                                            onClick={(event) => event.stopPropagation()}
                                        >
                                            <button
                                                className="sidebar-file-menu-btn"
                                                type="button"
                                                title={`File actions for "${file.fileName}"`}
                                                aria-label={`File actions for ${file.fileName}`}
                                                aria-haspopup="menu"
                                                aria-expanded={openFileActionMenuId === file.fileId}
                                                disabled={isFilePending}
                                                onClick={() => {
                                                    setIsCollectionSwitcherOpen(false);
                                                    setIsCollectionActionMenuOpen(false);
                                                    setOpenFileActionMenuId((current) =>
                                                        current === file.fileId ? null : file.fileId
                                                    );
                                                }}
                                            >
                                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                                    <circle cx="12" cy="5" r="1" />
                                                    <circle cx="12" cy="12" r="1" />
                                                    <circle cx="12" cy="19" r="1" />
                                                </svg>
                                            </button>

                                            {openFileActionMenuId === file.fileId && (
                                                <div className="sidebar-file-action-menu" role="menu">
                                                    <button
                                                        type="button"
                                                        role="menuitem"
                                                        className="sidebar-file-action-item"
                                                        disabled={isFilePending}
                                                        onClick={() => startRename(file.fileId, file.fileName)}
                                                    >
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                                            <path d="M12 20h9" />
                                                            <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
                                                        </svg>
                                                        Rename
                                                    </button>
                                                    <button
                                                        type="button"
                                                        role="menuitem"
                                                        className="sidebar-file-action-item danger"
                                                        disabled={isFilePending}
                                                        onClick={() => {
                                                            setOpenFileActionMenuId(null);
                                                            onRequestDeleteFile(file.fileId);
                                                        }}
                                                    >
                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                                            <path d="M3 6h18" />
                                                            <path d="M8 6V4h8v2" />
                                                            <path d="M19 6l-1 14H6L5 6" />
                                                            <path d="M10 11v5" />
                                                            <path d="M14 11v5" />
                                                        </svg>
                                                        Delete
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                            );
                        })
                    )}
                </div>
            </div>
        </aside>
    );
}
