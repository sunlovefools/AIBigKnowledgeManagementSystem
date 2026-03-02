import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import type { FileTabState, ParentChunkContent, SidebarFileSummary, DiffSegment } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");
const PAGE_SIZE = 7;

type UpdateParentChunkResponse = {
    parentId: string;
    previousParentId: string;
    fileName: string;
    content: string;
    size: number;
    chunks: number;
};

type UpdateFileResponse = {
    fileId: string;
    previousFileId: string;
    fileName: string;
    content: string;
    size: number;
    parentChunks: number;
    chunks: number;
};

type BatchTarget = {
    fileName: string;
    originalContent: string;
};

type LlmEditPreviewBatchResponse = {
    selectionMode: "manual" | "auto";
    selectedFiles: Array<{
        fileName: string;
        score?: number | null;
        reasons?: string[];
    }>;
    results: Array<{
        fileName: string;
        ok: boolean;
        editedContent?: string | null;
        summary?: string | null;
        warnings?: string[];
        error?: string | null;
    }>;
    stats: {
        total: number;
        success: number;
        failed: number;
    };
};

type AiBatchPreviewItem = {
    fileName: string;
    ok: boolean;
    score: number | null;
    reasons: string[];
    summary: string | null;
    warnings: string[];
    error: string | null;
    diffSegments: DiffSegment[];
    decision: "pending" | "accepted" | "rejected";
    saveState: "idle" | "saving" | "saved" | "failed";
};

type RequestAiPreviewResult = {
    ok: boolean;
    hasChanges?: boolean;
    summary?: string;
    error?: string;
};

type RequestAiPreviewOptions = {
    selectedFileNames?: string[];
};

type BatchSaveResult = {
    ok: boolean;
    saved: number;
    failed: number;
    skipped: number;
    message: string;
    closeAfterSave?: boolean;
};

function buildPreviewText(content: string): string {
    return content.replace(/\s+/g, " ").trim().slice(0, 160);
}

function createEmptyTabState(): FileTabState {
    return {
        chunks: [],
        hasMore: true,
        nextCursor: null,
        isLoading: false,
        isInitialized: false,
        error: null,
    };
}

// Simple line-level diff calculation for visualizing changes
function calculateDiffSegments(original: string, edited: string): DiffSegment[] {
    const originalLines = original.split(/\r?\n/);
    const editedLines = edited.split(/\r?\n/);
    
    const segments: DiffSegment[] = [];
    const maxLines = Math.max(originalLines.length, editedLines.length);
    
    for (let i = 0; i < maxLines; i++) {
        const origLine = originalLines[i] ?? "";
        const editedLine = editedLines[i] ?? "";
        
        if (origLine === editedLine) {
            if (origLine.length > 0) {
                segments.push({ type: "equal", text: origLine });
            }
        } else {
            if (origLine.length > 0) {
                segments.push({ type: "del", text: origLine });
            }
            if (editedLine.length > 0) {
                segments.push({ type: "add", text: editedLine });
            }
        }
    }
    
    return segments.length > 0 ? segments : [{ type: "equal", text: original }];
}

// Custom hook to manage sidebar files and tabbed full-view state.
export function useDocuments(isModificationPanelOpen: boolean) {
    const [files, setFiles] = useState<SidebarFileSummary[]>([]);
    const [isLoadingFiles, setIsLoadingFiles] = useState(false);
    const [fileListError, setFileListError] = useState<string | null>(null);
    const [isDocsCached, setIsDocsCached] = useState(false);
    const [openTabs, setOpenTabs] = useState<string[]>([]);
    const [activeTab, setActiveTab] = useState<string | null>(null);
    const [tabStates, setTabStates] = useState<Record<string, FileTabState>>({});
    const [selectedParentByTab, setSelectedParentByTab] = useState<Record<string, string | null>>({});
    const [editingParentId, setEditingParentId] = useState<string | null>(null);
    const [editingDraftByParentId, setEditingDraftByParentId] = useState<Record<string, string>>({});
    const [savingParentId, setSavingParentId] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [editingFileName, setEditingFileName] = useState<string | null>(null);
    const [editingDraftByFileName, setEditingDraftByFileName] = useState<Record<string, string>>({});
    const [savingFileName, setSavingFileName] = useState<string | null>(null);
    const [isAiEditGenerating, setIsAiEditGenerating] = useState(false);
    const [aiEditSummary, setAiEditSummary] = useState<string | null>(null);
    const [aiEditWarnings, setAiEditWarnings] = useState<string[]>([]);
    const [aiEditProposedContent, setAiEditProposedContent] = useState<string | null>(null);
    const [aiEditDiffSegments, setAiEditDiffSegments] = useState<DiffSegment[]>([]);
    const [aiEditError, setAiEditError] = useState<string | null>(null);
    const [aiBatchSelectionMode, setAiBatchSelectionMode] = useState<"manual" | "auto" | null>(null);
    const [aiBatchSelectedFiles, setAiBatchSelectedFiles] = useState<Array<{ fileName: string; score?: number | null; reasons: string[] }>>([]);
    const [aiBatchResults, setAiBatchResults] = useState<LlmEditPreviewBatchResponse["results"]>([]);
    const [aiBatchPreviewItems, setAiBatchPreviewItems] = useState<AiBatchPreviewItem[]>([]);
    const [isSavingAiBatch, setIsSavingAiBatch] = useState(false);
    const [aiBatchSaveMessage, setAiBatchSaveMessage] = useState<string | null>(null);
    const [aiBatchSaveError, setAiBatchSaveError] = useState<string | null>(null);

    const getFileIdByName = useCallback(
        (fileName: string) => files.find((item) => item.fileName === fileName)?.fileId ?? null,
        [files]
    );

    const fetchFiles = useCallback(async () => {
        setIsLoadingFiles(true);
        setFileListError(null);
        try {
            const response = await axios.get(`${API_BASE}/api/retrieve/all-preview-files`);
            const incomingFiles = (response.data.files ?? []) as SidebarFileSummary[];
            setFiles(incomingFiles);
            setIsDocsCached(true);

            const validNames = new Set(incomingFiles.map((item) => item.fileName));

            setOpenTabs((previousTabs) => previousTabs.filter((fileName) => validNames.has(fileName)));
            setSelectedParentByTab((previousSelections) => {
                const nextSelections: Record<string, string | null> = {};
                Object.entries(previousSelections).forEach(([fileName, parentId]) => {
                    if (validNames.has(fileName)) {
                        nextSelections[fileName] = parentId;
                    }
                });
                return nextSelections;
            });
            setTabStates((previousStates) => {
                const nextStates: Record<string, FileTabState> = {};
                Object.entries(previousStates).forEach(([fileName, state]) => {
                    if (validNames.has(fileName)) {
                        nextStates[fileName] = state;
                    }
                });
                return nextStates;
            });
            setActiveTab((previousActiveTab) =>
                previousActiveTab && validNames.has(previousActiveTab) ? previousActiveTab : null
            );
        } catch (error) {
            console.error("Error fetching documents:", error);
            setFileListError("Failed to load files from vector database.");
        } finally {
            setIsLoadingFiles(false);
        }
    }, []);

    useEffect(() => {
        if (isModificationPanelOpen && !isDocsCached) {
            void fetchFiles();
        }
    }, [isDocsCached, isModificationPanelOpen, fetchFiles]);

    const loadFileChunks = useCallback(
        async (fileName: string, reset = false) => {
            const fileId = getFileIdByName(fileName);
            if (!fileId) {
                setTabStates((previousStates) => {
                    const state = previousStates[fileName] ?? createEmptyTabState();
                    return {
                        ...previousStates,
                        [fileName]: {
                            ...state,
                            isLoading: false,
                            isInitialized: true,
                            error: `Missing file ID for ${fileName}. Please refresh documents.`,
                        },
                    };
                });
                return;
            }

            const currentState = tabStates[fileName] ?? createEmptyTabState();

            if (currentState.isLoading) {
                return;
            }

            if (!reset && currentState.isInitialized && !currentState.hasMore) {
                return;
            }

            setTabStates((previousStates) => {
                const state = previousStates[fileName] ?? createEmptyTabState();
                return {
                    ...previousStates,
                    [fileName]: {
                        ...state,
                        ...(reset
                            ? {
                                  chunks: [],
                                  nextCursor: null,
                                  hasMore: true,
                                  isInitialized: false,
                              }
                            : {}),
                        isLoading: true,
                        error: null,
                    },
                };
            });

            const cursor = reset ? null : currentState.nextCursor;

            try {
                const response = await axios.get(`${API_BASE}/api/retrieve/file-chunks`, {
                    params: {
                        fileId,
                        limit: PAGE_SIZE,
                        ...(cursor ? { cursor } : {}),
                    },
                });

                const incomingChunks = (response.data.chunks ?? []) as ParentChunkContent[];

                setTabStates((previousStates) => {
                    const state = previousStates[fileName] ?? createEmptyTabState();
                    const mergedChunks = reset ? incomingChunks : [...state.chunks, ...incomingChunks];
                    const dedupedChunks = Array.from(
                        new Map(mergedChunks.map((chunk) => [chunk.parentId, chunk])).values()
                    );

                    return {
                        ...previousStates,
                        [fileName]: {
                            ...state,
                            chunks: dedupedChunks,
                            hasMore: Boolean(response.data.hasMore),
                            nextCursor: response.data.nextCursor ?? null,
                            isLoading: false,
                            isInitialized: true,
                            error: null,
                        },
                    };
                });

                setSelectedParentByTab((previousSelections) => {
                    const previousSelected = previousSelections[fileName] ?? null;
                    const containsPrevious = incomingChunks.some((chunk) => chunk.parentId === previousSelected);
                    const nextSelected =
                        previousSelected && (containsPrevious || !reset)
                            ? previousSelected
                            : (incomingChunks[0]?.parentId ?? previousSelected ?? null);

                    return {
                        ...previousSelections,
                        [fileName]: nextSelected,
                    };
                });
            } catch (error) {
                console.error(`Error loading chunks for ${fileName}:`, error);
                setTabStates((previousStates) => {
                    const state = previousStates[fileName] ?? createEmptyTabState();
                    return {
                        ...previousStates,
                        [fileName]: {
                            ...state,
                            isLoading: false,
                            isInitialized: true,
                            error: `Failed to load document content for ${fileName}.`,
                        },
                    };
                });
            }
        },
        [getFileIdByName, tabStates]
    );

    const getChunkContentByParentId = useCallback(
        (parentId: string | null) => {
            if (!parentId) {
                return null;
            }

            for (const tabState of Object.values(tabStates)) {
                const matchedChunk = tabState.chunks.find((chunk) => chunk.parentId === parentId);
                if (matchedChunk) {
                    return matchedChunk.content;
                }
            }

            return null;
        },
        [tabStates]
    );

    const getFullDocumentContentByFileName = useCallback(
        (fileName: string | null) => {
            if (!fileName) {
                return "";
            }

            const fileState = tabStates[fileName] ?? createEmptyTabState();
            return fileState.chunks
                .map((chunk) => chunk.content)
                .join("\n\n")
                .trim();
        },
        [tabStates]
    );

    const clearEditingState = useCallback(() => {
        setEditingParentId((previousEditingParentId) => {
            if (!previousEditingParentId) {
                return null;
            }

            setEditingDraftByParentId((previousDrafts) => {
                if (!(previousEditingParentId in previousDrafts)) {
                    return previousDrafts;
                }

                const { [previousEditingParentId]: _removedDraft, ...nextDrafts } = previousDrafts;
                return nextDrafts;
            });

            return null;
        });

        setEditingFileName((previousEditingFileName) => {
            if (!previousEditingFileName) {
                return null;
            }

            setEditingDraftByFileName((previousDrafts) => {
                if (!(previousEditingFileName in previousDrafts)) {
                    return previousDrafts;
                }

                const { [previousEditingFileName]: _removedDraft, ...nextDrafts } = previousDrafts;
                return nextDrafts;
            });

            return null;
        });
        setSaveError(null);
        setIsAiEditGenerating(false);
        setAiEditSummary(null);
        setAiEditWarnings([]);
        setAiEditProposedContent(null);
        setAiEditDiffSegments([]);
        setAiEditError(null);
        setAiBatchSelectionMode(null);
        setAiBatchSelectedFiles([]);
        setAiBatchResults([]);
        setAiBatchPreviewItems([]);
        setIsSavingAiBatch(false);
        setAiBatchSaveMessage(null);
        setAiBatchSaveError(null);
    }, []);

    const clearAiEditProposal = useCallback(() => {
        setAiEditSummary(null);
        setAiEditWarnings([]);
        setAiEditProposedContent(null);
        setAiEditDiffSegments([]);
        setAiEditError(null);
        setAiBatchSelectionMode(null);
        setAiBatchSelectedFiles([]);
        setAiBatchResults([]);
        setAiBatchPreviewItems([]);
        setIsSavingAiBatch(false);
        setAiBatchSaveMessage(null);
        setAiBatchSaveError(null);
    }, []);

    const confirmDiscardUnsavedChanges = useCallback(() => {
        if (!editingParentId && !editingFileName) {
            return true;
        }

        let hasUnsavedChanges = false;

        if (editingParentId) {
            const originalContent = getChunkContentByParentId(editingParentId) ?? "";
            const draftContent = editingDraftByParentId[editingParentId] ?? originalContent;
            hasUnsavedChanges = draftContent !== originalContent;
        }

        if (!hasUnsavedChanges && editingFileName) {
            const originalContent = getFullDocumentContentByFileName(editingFileName);
            const draftContent = editingDraftByFileName[editingFileName] ?? originalContent;
            hasUnsavedChanges = draftContent !== originalContent;
        }

        if (!hasUnsavedChanges) {
            clearEditingState();
            return true;
        }

        const shouldDiscard = window.confirm("You have unsaved changes. Discard them?");
        if (!shouldDiscard) {
            return false;
        }

        clearEditingState();
        return true;
    }, [
        clearEditingState,
        editingDraftByFileName,
        editingDraftByParentId,
        editingFileName,
        editingParentId,
        getChunkContentByParentId,
        getFullDocumentContentByFileName,
    ]);

    const openDocumentTab = useCallback(
        async (fileName: string) => {
            if (!confirmDiscardUnsavedChanges()) {
                return;
            }

            setOpenTabs((previousTabs) =>
                previousTabs.includes(fileName) ? previousTabs : [...previousTabs, fileName]
            );
            setActiveTab(fileName);

            const state = tabStates[fileName];
            if (!state || !state.isInitialized) {
                await loadFileChunks(fileName, false);
            }
        },
        [confirmDiscardUnsavedChanges, loadFileChunks, tabStates]
    );

    const closeDocumentTab = useCallback((fileName: string) => {
        if (!confirmDiscardUnsavedChanges()) {
            return;
        }

        setOpenTabs((previousTabs) => {
            const index = previousTabs.indexOf(fileName);
            if (index < 0) {
                return previousTabs;
            }

            const nextTabs = previousTabs.filter((name) => name !== fileName);

            setActiveTab((previousActiveTab) => {
                if (previousActiveTab !== fileName) {
                    return previousActiveTab;
                }

                if (nextTabs.length === 0) {
                    return null;
                }

                const nextIndex = Math.min(index, nextTabs.length - 1);
                return nextTabs[nextIndex];
            });

            return nextTabs;
        });

        setSelectedParentByTab((previousSelections) => {
            if (!(fileName in previousSelections)) {
                return previousSelections;
            }

            const { [fileName]: _removedSelection, ...nextSelections } = previousSelections;
            return nextSelections;
        });
    }, [confirmDiscardUnsavedChanges]);

    const setActiveDocumentTab = useCallback(
        async (fileName: string) => {
            if (!confirmDiscardUnsavedChanges()) {
                return;
            }

            setActiveTab(fileName);
            const state = tabStates[fileName];
            if (!state || !state.isInitialized) {
                await loadFileChunks(fileName, false);
            }
        },
        [confirmDiscardUnsavedChanges, loadFileChunks, tabStates]
    );

    const loadMoreActiveTab = useCallback(async () => {
        if (!activeTab) {
            return;
        }
        await loadFileChunks(activeTab, false);
    }, [activeTab, loadFileChunks]);

    const handleRefreshDocuments = useCallback(async () => {
        if (!confirmDiscardUnsavedChanges()) {
            return;
        }

        await fetchFiles();
    }, [confirmDiscardUnsavedChanges, fetchFiles]);

    const invalidateDocumentCache = useCallback(() => {
        setIsDocsCached(false);
    }, []);

    const activeTabState = useMemo(
        () => (activeTab ? tabStates[activeTab] ?? createEmptyTabState() : null),
        [activeTab, tabStates]
    );

    const activeSelectedParentId = useMemo(
        () => (activeTab ? selectedParentByTab[activeTab] ?? null : null),
        [activeTab, selectedParentByTab]
    );

    const activeSelectedChunk = useMemo(
        () => activeTabState?.chunks.find((chunk) => chunk.parentId === activeSelectedParentId) ?? null,
        [activeSelectedParentId, activeTabState]
    );

    const editingContent = useMemo(() => {
        if (!editingParentId) {
            return "";
        }
        const fallbackContent = getChunkContentByParentId(editingParentId) ?? "";
        return editingDraftByParentId[editingParentId] ?? fallbackContent;
    }, [editingDraftByParentId, editingParentId, getChunkContentByParentId]);

    const isEditingActiveChunk = Boolean(
        activeSelectedParentId && editingParentId && activeSelectedParentId === editingParentId
    );

    const isSavingActiveChunk = Boolean(
        activeSelectedParentId && savingParentId && activeSelectedParentId === savingParentId
    );

    const isActiveChunkDirty = useMemo(() => {
        if (!activeSelectedParentId || !isEditingActiveChunk) {
            return false;
        }

        const originalContent = activeSelectedChunk?.content ?? "";
        const draftContent = editingDraftByParentId[activeSelectedParentId] ?? originalContent;
        return draftContent !== originalContent;
    }, [activeSelectedChunk, activeSelectedParentId, editingDraftByParentId, isEditingActiveChunk]);

    const selectActiveTabChunk = useCallback(
        (parentId: string) => {
            if (!activeTab) {
                return;
            }

            if (editingParentId && parentId !== editingParentId && !confirmDiscardUnsavedChanges()) {
                return;
            }

            setSelectedParentByTab((previousSelections) => ({
                ...previousSelections,
                [activeTab]: parentId,
            }));
            setSaveError(null);
        },
        [activeTab, confirmDiscardUnsavedChanges, editingParentId]
    );

    const startEditingActiveChunk = useCallback(() => {
        if (!activeSelectedChunk) {
            return;
        }

        setEditingParentId(activeSelectedChunk.parentId);
        setEditingDraftByParentId((previousDrafts) => ({
            ...previousDrafts,
            [activeSelectedChunk.parentId]: previousDrafts[activeSelectedChunk.parentId] ?? activeSelectedChunk.content,
        }));
        setSaveError(null);
    }, [activeSelectedChunk]);

    const setActiveEditingContent = useCallback(
        (nextContent: string) => {
            if (!editingParentId) {
                return;
            }

            setEditingDraftByParentId((previousDrafts) => ({
                ...previousDrafts,
                [editingParentId]: nextContent,
            }));
        },
        [editingParentId]
    );

    const cancelEditingActiveChunk = useCallback(() => {
        clearEditingState();
    }, [clearEditingState]);

    const saveEditingActiveChunk = useCallback(async () => {
        if (!activeTab || !editingParentId || savingParentId) {
            return false;
        }

        const originalContent = getChunkContentByParentId(editingParentId) ?? "";
        const draftContent = editingDraftByParentId[editingParentId] ?? originalContent;
        const trimmedDraftContent = draftContent.trim();

        if (!trimmedDraftContent) {
            setSaveError("Content cannot be empty.");
            return false;
        }

        if (draftContent === originalContent) {
            clearEditingState();
            return true;
        }

        setSavingParentId(editingParentId);
        setSaveError(null);

        try {
            const response = await axios.put<UpdateParentChunkResponse>(
                `${API_BASE}/api/modifications/parent-chunks/${editingParentId}`,
                {
                    fileName: activeTab,
                    content: draftContent,
                }
            );

            const updatedParentId = response.data.parentId;
            await loadFileChunks(activeTab, true);

            setSelectedParentByTab((previousSelections) => ({
                ...previousSelections,
                [activeTab]: updatedParentId,
            }));
            clearEditingState();
            return true;
        } catch (error) {
            console.error("Error saving parent chunk:", error);
            setSaveError("Failed to save document changes. Please try again.");
            return false;
        } finally {
            setSavingParentId(null);
        }
    }, [
        activeTab,
        clearEditingState,
        editingDraftByParentId,
        editingParentId,
        getChunkContentByParentId,
        loadFileChunks,
        savingParentId,
    ]);

    const editingDocumentContent = useMemo(() => {
        if (!editingFileName) {
            return "";
        }
        const fallbackContent = getFullDocumentContentByFileName(editingFileName);
        return editingDraftByFileName[editingFileName] ?? fallbackContent;
    }, [editingDraftByFileName, editingFileName, getFullDocumentContentByFileName]);

    const isEditingActiveDocument = Boolean(activeTab && editingFileName && activeTab === editingFileName);

    const isSavingActiveDocument = Boolean(activeTab && savingFileName && activeTab === savingFileName);

    const isActiveDocumentDirty = useMemo(() => {
        if (!activeTab || !isEditingActiveDocument) {
            return false;
        }

        const originalContent = getFullDocumentContentByFileName(activeTab);
        const draftContent = editingDraftByFileName[activeTab] ?? originalContent;
        return draftContent !== originalContent;
    }, [activeTab, editingDraftByFileName, getFullDocumentContentByFileName, isEditingActiveDocument]);

    const startEditingActiveDocument = useCallback(() => {
        if (!activeTab || !activeTabState?.chunks.length) {
            return;
        }

        const fullContent = getFullDocumentContentByFileName(activeTab);
        setEditingFileName(activeTab);
        setEditingDraftByFileName((previousDrafts) => ({
            ...previousDrafts,
            [activeTab]: previousDrafts[activeTab] ?? fullContent,
        }));
        setSaveError(null);
    }, [activeTab, activeTabState?.chunks.length, getFullDocumentContentByFileName]);

    const setActiveEditingDocumentContent = useCallback(
        (nextContent: string) => {
            if (!editingFileName) {
                return;
            }

            setEditingDraftByFileName((previousDrafts) => ({
                ...previousDrafts,
                [editingFileName]: nextContent,
            }));

            setAiEditSummary(null);
            setAiEditWarnings([]);
            setAiEditProposedContent(null);
            setAiEditError(null);
        },
        [editingFileName]
    );

    const cancelEditingActiveDocument = useCallback(() => {
        clearEditingState();
    }, [clearEditingState]);

    const requestAiEditPreview = useCallback(
        async (instruction: string, options?: RequestAiPreviewOptions): Promise<RequestAiPreviewResult> => {
            const trimmedInstruction = instruction.trim();

            if (isAiEditGenerating) {
                return { ok: false, error: "AI edit preview is already generating." };
            }

            if (!trimmedInstruction) {
                const error = "Instruction cannot be empty.";
                setAiEditError(error);
                return { ok: false, error };
            }

            setIsAiEditGenerating(true);
            setAiEditError(null);
            setAiEditSummary(null);
            setAiEditWarnings([]);
            setAiEditProposedContent(null);
            setAiEditDiffSegments([]);
            setAiBatchSelectionMode(null);
            setAiBatchSelectedFiles([]);
            setAiBatchResults([]);
            setAiBatchPreviewItems([]);
            setAiBatchSaveMessage(null);
            setAiBatchSaveError(null);

            try {
                const selectedFileNames = Array.from(
                    new Set((options?.selectedFileNames ?? []).map((name) => name.trim()).filter(Boolean))
                );
                const isManualMode = selectedFileNames.length > 0;

                const candidateNames = Array.from(
                    new Set([activeTab, ...openTabs].filter((name): name is string => Boolean(name && name.trim())))
                );

                const contentByFileName = new Map<string, string>();
                for (const fileName of candidateNames) {
                    const content =
                        (editingFileName && editingFileName === fileName
                            ? editingDraftByFileName[fileName]
                            : undefined) ?? getFullDocumentContentByFileName(fileName);
                    if (content && content.trim()) {
                        contentByFileName.set(fileName, content);
                    }
                }

                const previewByFileName = new Map(
                    files.map((file) => [file.fileName, file.previewTexts || ""])
                );

                const manualTargets: BatchTarget[] = isManualMode
                    ? selectedFileNames
                          .map((fileName) => {
                              const content =
                                  contentByFileName.get(fileName) ??
                                  previewByFileName.get(fileName) ??
                                  "";
                              return { fileName, originalContent: content };
                          })
                          .filter((item) => item.originalContent.trim().length > 0)
                    : [];

                if (isManualMode && manualTargets.length === 0) {
                    const error = "Selected files are missing loaded content. Please open selected files first.";
                    setAiEditError(error);
                    return { ok: false, error };
                }

                if (!isManualMode && files.length === 0) {
                    const error = "No files available for auto selection.";
                    setAiEditError(error);
                    return { ok: false, error };
                }

                const autoCandidates: BatchTarget[] = isManualMode
                    ? []
                    : files
                          .map((file) => {
                              const originalContent =
                                  contentByFileName.get(file.fileName) ??
                                  previewByFileName.get(file.fileName) ??
                                  "";
                              return {
                                  fileName: file.fileName,
                                  originalContent,
                              };
                          })
                          .filter((item) => item.originalContent.trim().length > 0);

                const requestTargetContentByFileName = new Map<string, string>([
                    ...manualTargets.map((item) => [item.fileName, item.originalContent] as const),
                    ...autoCandidates.map((item) => [item.fileName, item.originalContent] as const),
                ]);

                const response = await axios.post<LlmEditPreviewBatchResponse>(
                    `${API_BASE}/api/modifications/llm-edit-preview-batch`,
                    {
                        instruction: trimmedInstruction,
                        selectionMode: isManualMode ? "manual" : "auto",
                        targets: manualTargets,
                        autoCandidates,
                        activeFileName: activeTab,
                        autoSelectOptions: {
                            maxFiles: 3,
                            minScore: 0.55,
                        },
                    }
                );

                const batch = response.data;
                setAiBatchSelectionMode(batch.selectionMode);
                const normalizedSelectedFiles = (batch.selectedFiles ?? []).map((item) => ({
                        fileName: item.fileName,
                        score: item.score,
                        reasons: Array.isArray(item.reasons)
                            ? item.reasons.filter((reason): reason is string => typeof reason === "string" && reason.trim().length > 0)
                            : [],
                    }));
                setAiBatchSelectedFiles(normalizedSelectedFiles);
                setAiBatchResults(batch.results ?? []);

                const selectedMetaByFileName = new Map(
                    normalizedSelectedFiles.map((item) => [item.fileName, item])
                );
                const normalizedPreviewItems: AiBatchPreviewItem[] = (batch.results ?? []).map((item) => {
                    const meta = selectedMetaByFileName.get(item.fileName);
                    const score = typeof meta?.score === "number" ? meta.score : null;
                    const reasons = meta?.reasons ?? [];

                    if (!item.ok) {
                        return {
                            fileName: item.fileName,
                            ok: false,
                            score,
                            reasons,
                            summary: null,
                            warnings: [],
                            error: item.error ?? "Unknown error",
                            diffSegments: [],
                            decision: "pending",
                            saveState: "idle",
                        };
                    }

                    const source =
                        requestTargetContentByFileName.get(item.fileName) ??
                        contentByFileName.get(item.fileName) ??
                        "";
                    const edited =
                        typeof item.editedContent === "string" && item.editedContent.length > 0
                            ? item.editedContent
                            : source;
                    const warnings = Array.isArray(item.warnings)
                        ? item.warnings.filter((warning): warning is string => typeof warning === "string" && warning.trim().length > 0)
                        : [];

                    return {
                        fileName: item.fileName,
                        ok: true,
                        score,
                        reasons,
                        summary: item.summary?.trim() || "AI edit preview generated.",
                        warnings,
                        error: null,
                        diffSegments: calculateDiffSegments(source, edited),
                        decision: "pending",
                        saveState: "idle",
                    };
                });
                setAiBatchPreviewItems(normalizedPreviewItems);

                const hasBatchChanges = normalizedPreviewItems.some(
                    (item) => item.ok && item.diffSegments.some((segment) => segment.type === "add" || segment.type === "del")
                );

                const successResults = (batch.results ?? []).filter((item) => item.ok);
                if (successResults.length === 0) {
                    setAiEditProposedContent(null);
                    setAiEditSummary(null);
                    setAiEditWarnings([]);
                    setAiEditDiffSegments([]);
                    return { ok: true, hasChanges: false, summary: "No relevant files matched the instruction." };
                }

                const preferredResult =
                    successResults.find((item) => item.fileName === activeTab) ?? successResults[0];
                const preferredSourceContent =
                    requestTargetContentByFileName.get(preferredResult.fileName) ??
                    contentByFileName.get(preferredResult.fileName) ??
                    "";

                const editedContent =
                    typeof preferredResult.editedContent === "string" && preferredResult.editedContent.trim().length > 0
                        ? preferredResult.editedContent
                        : preferredSourceContent;
                const baseSummary = (preferredResult.summary || "AI edit preview generated.").trim();
                const summary =
                    batch.stats.total > 1
                        ? `${baseSummary} (${batch.stats.success}/${batch.stats.total} files succeeded)`
                        : baseSummary;

                const warnings = Array.isArray(preferredResult.warnings)
                    ? preferredResult.warnings.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
                    : [];

                const diffSegments = calculateDiffSegments(preferredSourceContent, editedContent);

                const hasPreferredChanges = diffSegments.some(
                    (segment) => segment.type === "add" || segment.type === "del"
                );

                setAiEditProposedContent(editedContent);
                setAiEditSummary(summary);
                setAiEditWarnings(warnings);
                setAiEditDiffSegments(diffSegments);

                return { ok: true, summary, hasChanges: hasBatchChanges || hasPreferredChanges };
            } catch (error) {
                console.error("Error requesting AI edit preview:", error);
                const errorMessage = "Failed to generate AI edit preview. Please try again.";
                setAiEditError(errorMessage);
                return { ok: false, error: errorMessage };
            } finally {
                setIsAiEditGenerating(false);
            }
        },
        [
            activeTab,
            editingDraftByFileName,
            editingFileName,
            files,
            getFullDocumentContentByFileName,
            isAiEditGenerating,
            openTabs,
        ]
    );

    const acceptAiEditProposal = useCallback(() => {
        if (!activeTab || aiEditProposedContent === null) {
            return false;
        }

        setEditingFileName(activeTab);
        setEditingDraftByFileName((previousDrafts) => ({
            ...previousDrafts,
            [activeTab]: aiEditProposedContent,
        }));
        clearAiEditProposal();
        return true;
    }, [activeTab, aiEditProposedContent, clearAiEditProposal]);

    const rejectAiEditProposal = useCallback(() => {
        clearAiEditProposal();
    }, [clearAiEditProposal]);

    const acceptAiBatchFileProposal = useCallback(
        (fileName: string) => {
            const matched = aiBatchResults.find((item) => item.fileName === fileName && item.ok);
            const editedContent = matched?.editedContent;

            if (!matched || typeof editedContent !== "string") {
                return false;
            }

            setEditingDraftByFileName((previousDrafts) => ({
                ...previousDrafts,
                [fileName]: editedContent,
            }));

            setAiBatchPreviewItems((previousItems) =>
                previousItems.map((item) =>
                    item.fileName === fileName
                        ? { ...item, decision: "accepted", saveState: "idle" }
                        : item
                )
            );

            return true;
        },
        [aiBatchResults]
    );

    const rejectAiBatchFileProposal = useCallback((fileName: string) => {
        setAiBatchPreviewItems((previousItems) =>
            previousItems.map((item) =>
                item.fileName === fileName
                    ? { ...item, decision: "rejected", saveState: "idle" }
                    : item
            )
        );
    }, []);

    const saveAcceptedAiBatchFiles = useCallback(async (): Promise<BatchSaveResult> => {
        if (isSavingAiBatch) {
            return {
                ok: false,
                saved: 0,
                failed: 0,
                skipped: 0,
                message: "Batch save is already in progress.",
                closeAfterSave: false,
            };
        }

        const actionableItems = aiBatchPreviewItems.filter((item) => item.ok);
        const allRejected =
            actionableItems.length > 0 &&
            actionableItems.every((item) => item.decision === "rejected");

        const acceptedItems = aiBatchPreviewItems.filter(
            (item) => item.ok && item.decision === "accepted" && item.saveState !== "saved"
        );

        if (acceptedItems.length === 0) {
            if (allRejected) {
                const message = "All files were rejected. Closing edit panel.";
                setAiBatchSaveMessage(message);
                setAiBatchSaveError(null);
                clearAiEditProposal();
                return {
                    ok: true,
                    saved: 0,
                    failed: 0,
                    skipped: actionableItems.length,
                    message,
                    closeAfterSave: true,
                };
            }

            const message = "No accepted files to save.";
            setAiBatchSaveMessage(message);
            setAiBatchSaveError(null);
            return { ok: true, saved: 0, failed: 0, skipped: 0, message, closeAfterSave: false };
        }

        setIsSavingAiBatch(true);
        setAiBatchSaveError(null);
        setAiBatchSaveMessage(null);

        setAiBatchPreviewItems((previousItems) =>
            previousItems.map((item) =>
                acceptedItems.some((accepted) => accepted.fileName === item.fileName)
                    ? { ...item, saveState: "saving" }
                    : item
            )
        );

        let saved = 0;
        let failed = 0;
        let skipped = 0;

        for (const item of acceptedItems) {
            const fileName = item.fileName;
            const fileId = getFileIdByName(fileName);

            const originalContent = getFullDocumentContentByFileName(fileName);
            const matched = aiBatchResults.find((result) => result.fileName === fileName && result.ok);
            const matchedEdited = matched?.editedContent;
            const draftContent =
                editingDraftByFileName[fileName] ??
                (typeof matchedEdited === "string" ? matchedEdited : originalContent);

            if (!fileId) {
                failed += 1;
                setAiBatchPreviewItems((previousItems) =>
                    previousItems.map((current) =>
                        current.fileName === fileName ? { ...current, saveState: "failed" } : current
                    )
                );
                continue;
            }

            if (!draftContent.trim()) {
                failed += 1;
                setAiBatchPreviewItems((previousItems) =>
                    previousItems.map((current) =>
                        current.fileName === fileName ? { ...current, saveState: "failed" } : current
                    )
                );
                continue;
            }

            if (draftContent === originalContent) {
                skipped += 1;
                setAiBatchPreviewItems((previousItems) =>
                    previousItems.map((current) =>
                        current.fileName === fileName ? { ...current, saveState: "saved" } : current
                    )
                );
                continue;
            }

            try {
                const response = await axios.put<UpdateFileResponse>(`${API_BASE}/api/modifications/update-file/${fileId}`, {
                    fileName,
                    content: draftContent,
                });

                const updated = response.data;
                const localParentId =
                    tabStates[fileName]?.chunks[0]?.parentId ?? `local-${updated.fileId || fileName}`;

                setFiles((previousFiles) =>
                    previousFiles.map((file) =>
                        file.fileName === fileName || file.fileId === updated.previousFileId
                            ? {
                                  ...file,
                                  fileId: updated.fileId,
                                  fileName: updated.fileName,
                                  previewTexts: buildPreviewText(updated.content),
                              }
                            : file
                    )
                );

                setTabStates((previousStates) => {
                    const current = previousStates[fileName] ?? createEmptyTabState();
                    return {
                        ...previousStates,
                        [fileName]: {
                            ...current,
                            chunks: [
                                {
                                    parentId: localParentId,
                                    content: updated.content,
                                    size: updated.size,
                                },
                            ],
                            hasMore: false,
                            nextCursor: null,
                            isLoading: false,
                            isInitialized: true,
                            error: null,
                        },
                    };
                });

                setSelectedParentByTab((previousSelections) => ({
                    ...previousSelections,
                    [fileName]: localParentId,
                }));

                setEditingDraftByFileName((previousDrafts) => {
                    if (!(fileName in previousDrafts)) {
                        return previousDrafts;
                    }
                    const { [fileName]: _removed, ...nextDrafts } = previousDrafts;
                    return nextDrafts;
                });

                setAiBatchPreviewItems((previousItems) =>
                    previousItems.map((current) =>
                        current.fileName === fileName ? { ...current, saveState: "saved" } : current
                    )
                );
                saved += 1;
            } catch (error) {
                console.error(`Error saving batch file ${fileName}:`, error);
                failed += 1;
                setAiBatchPreviewItems((previousItems) =>
                    previousItems.map((current) =>
                        current.fileName === fileName ? { ...current, saveState: "failed" } : current
                    )
                );
            }
        }

        const message = `Batch save completed. Saved: ${saved}, Failed: ${failed}, Skipped: ${skipped}.`;
        setAiBatchSaveMessage(message);
        setAiBatchSaveError(failed > 0 ? "Some accepted files failed to save. You can retry Save Accepted Files." : null);
        setIsSavingAiBatch(false);

        return {
            ok: failed === 0,
            saved,
            failed,
            skipped,
            message,
            closeAfterSave: false,
        };
    }, [
        aiBatchPreviewItems,
        aiBatchResults,
        clearAiEditProposal,
        editingDraftByFileName,
        getFileIdByName,
        getFullDocumentContentByFileName,
        isSavingAiBatch,
        tabStates,
    ]);

    const retryFailedAiBatchFiles = useCallback(() => {
        setAiBatchPreviewItems((previousItems) =>
            previousItems.map((item) =>
                item.decision === "accepted" && item.saveState === "failed"
                    ? { ...item, saveState: "idle" }
                    : item
            )
        );
        setAiBatchSaveError(null);
    }, []);

    const saveEditingActiveDocument = useCallback(async () => {
        if (!activeTab || !editingFileName || activeTab !== editingFileName || savingFileName) {
            return false;
        }

        const fileId = getFileIdByName(activeTab);
        if (!fileId) {
            setSaveError(`Missing file ID for ${activeTab}. Please refresh documents.`);
            return false;
        }

        const originalContent = getFullDocumentContentByFileName(activeTab);
        const draftContent = editingDraftByFileName[activeTab] ?? originalContent;
        const trimmedDraftContent = draftContent.trim();

        if (!trimmedDraftContent) {
            setSaveError("Content cannot be empty.");
            return false;
        }

        if (draftContent === originalContent) {
            clearEditingState();
            return true;
        }

        setSavingFileName(activeTab);
        setSaveError(null);

        try {
            const response = await axios.put<UpdateFileResponse>(`${API_BASE}/api/modifications/update-file/${fileId}`, {
                fileName: activeTab,
                content: draftContent,
            });

            const updated = response.data;
            const localParentId =
                tabStates[activeTab]?.chunks[0]?.parentId ?? `local-${updated.fileId || activeTab}`;

            setFiles((previousFiles) =>
                previousFiles.map((file) =>
                    file.fileName === activeTab || file.fileId === updated.previousFileId
                        ? {
                              ...file,
                              fileId: updated.fileId,
                              fileName: updated.fileName,
                              previewTexts: buildPreviewText(updated.content),
                          }
                        : file
                )
            );

            setTabStates((previousStates) => {
                const current = previousStates[activeTab] ?? createEmptyTabState();
                return {
                    ...previousStates,
                    [activeTab]: {
                        ...current,
                        chunks: [
                            {
                                parentId: localParentId,
                                content: updated.content,
                                size: updated.size,
                            },
                        ],
                        hasMore: false,
                        nextCursor: null,
                        isLoading: false,
                        isInitialized: true,
                        error: null,
                    },
                };
            });

            setSelectedParentByTab((previousSelections) => ({
                ...previousSelections,
                [activeTab]: localParentId,
            }));

            clearEditingState();
            return true;
        } catch (error) {
            console.error("Error saving full document:", error);
            setSaveError("Failed to save document changes. Please try again.");
            return false;
        } finally {
            setSavingFileName(null);
        }
    }, [
        activeTab,
        clearEditingState,
        editingDraftByFileName,
        editingFileName,
        getFileIdByName,
        getFullDocumentContentByFileName,
        savingFileName,
        tabStates,
    ]);

    return {
        files,
        isLoadingFiles,
        fileListError,
        openTabs,
        activeTab,
        activeTabState,
        tabStates,
        fetchFiles,
        handleRefreshDocuments,
        invalidateDocumentCache,
        openDocumentTab,
        closeDocumentTab,
        setActiveDocumentTab,
        loadMoreActiveTab,
        activeSelectedParentId,
        editingContent,
        isEditingActiveChunk,
        isSavingActiveChunk,
        isActiveChunkDirty,
        saveError,
        selectActiveTabChunk,
        startEditingActiveChunk,
        setActiveEditingContent,
        cancelEditingActiveChunk,
        saveEditingActiveChunk,
        editingDocumentContent,
        isEditingActiveDocument,
        isSavingActiveDocument,
        isActiveDocumentDirty,
        startEditingActiveDocument,
        setActiveEditingDocumentContent,
        cancelEditingActiveDocument,
        saveEditingActiveDocument,
        isAiEditGenerating,
        aiEditSummary,
        aiEditWarnings,
        aiEditDiffSegments,
        aiEditProposedContent,
        aiEditError,
        aiBatchSelectionMode,
        aiBatchSelectedFiles,
        aiBatchResults,
        aiBatchPreviewItems,
        isSavingAiBatch,
        aiBatchSaveMessage,
        aiBatchSaveError,
        hasAiEditProposal: aiEditProposedContent !== null,
        requestAiEditPreview,
        acceptAiEditProposal,
        rejectAiEditProposal,
        acceptAiBatchFileProposal,
        rejectAiBatchFileProposal,
        saveAcceptedAiBatchFiles,
        retryFailedAiBatchFiles,
    };
}
