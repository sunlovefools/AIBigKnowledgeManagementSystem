import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import type { AgentProposal, FileTabState, ParentChunkContent, SidebarFileSummary } from "../types";

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

type AgentModifyResponse = {
    intention: string;
    proposals: AgentProposal[];
};

type RequestAgentResult = {
    ok: boolean;
    summary?: string;
    error?: string;
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

export function useDocuments(isModificationPanelOpen: boolean) {
    const [files, setFiles] = useState<SidebarFileSummary[]>([]);
    const [isLoadingFiles, setIsLoadingFiles] = useState(false);
    const [fileListError, setFileListError] = useState<string | null>(null);
    const [isDocsCached, setIsDocsCached] = useState(false);

    // All tab state keyed by fileId (not fileName) to support same-name files.
    const [openTabs, setOpenTabs] = useState<string[]>([]);          // fileIds
    const [activeTab, setActiveTab] = useState<string | null>(null);  // fileId
    const [tabStates, setTabStates] = useState<Record<string, FileTabState>>({}); // fileId → state

    // Editing state, also keyed by fileId
    const [editingFileId, setEditingFileId] = useState<string | null>(null);
    const [editingDraftByFileId, setEditingDraftByFileId] = useState<Record<string, string>>({});
    const [savingFileId, setSavingFileId] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);

    // Agent state
    const [isAgentGenerating, setIsAgentGenerating] = useState(false);
    const [agentProposals, setAgentProposals] = useState<AgentProposal[]>([]);
    const [agentAcceptedMap, setAgentAcceptedMap] = useState<Map<string, AgentProposal>>(new Map());
    const [agentSavedIds, setAgentSavedIds] = useState<Set<string>>(new Set());
    const [agentRejectedIds, setAgentRejectedIds] = useState<Set<string>>(new Set());
    const [agentSavingIds, setAgentSavingIds] = useState<Set<string>>(new Set());
    const [agentError, setAgentError] = useState<string | null>(null);
    const [agentIntention, setAgentIntention] = useState<string | null>(null);

    // Helpers: resolve between fileId and fileName
    const getFileNameById = useCallback(
        (fileId: string) => files.find((f) => f.fileId === fileId)?.fileName ?? fileId,
        [files]
    );
    const getFileIdByName = useCallback(
        (fileName: string) => files.find((f) => f.fileName === fileName)?.fileId ?? null,
        [files]
    );

    // ── File list ──

    const fetchFiles = useCallback(async () => {
        setIsLoadingFiles(true);
        setFileListError(null);
        try {
            const response = await axios.get(`${API_BASE}/api/retrieve/all-preview-files`);
            const incoming = (response.data.files ?? []) as SidebarFileSummary[];
            setFiles(incoming);
            setIsDocsCached(true);
            const validIds = new Set(incoming.map((f) => f.fileId));
            setOpenTabs((prev) => prev.filter((id) => validIds.has(id)));
            setTabStates((prev) => {
                const next: Record<string, FileTabState> = {};
                Object.entries(prev).forEach(([id, s]) => { if (validIds.has(id)) next[id] = s; });
                return next;
            });
            setActiveTab((prev) => (prev && validIds.has(prev) ? prev : null));
        } catch {
            setFileListError("Failed to load files from vector database.");
        } finally {
            setIsLoadingFiles(false);
        }
    }, []);

    useEffect(() => {
        if (isModificationPanelOpen && !isDocsCached) void fetchFiles();
    }, [isDocsCached, isModificationPanelOpen, fetchFiles]);

    // ── Chunk loading ──

    const loadFileChunks = useCallback(
        async (fileId: string, reset = false) => {
            const current = tabStates[fileId] ?? createEmptyTabState();
            if (current.isLoading) return;
            if (!reset && current.isInitialized && !current.hasMore) return;

            setTabStates((prev) => ({
                ...prev,
                [fileId]: {
                    ...(prev[fileId] ?? createEmptyTabState()),
                    ...(reset ? { chunks: [], nextCursor: null, hasMore: true, isInitialized: false } : {}),
                    isLoading: true,
                    error: null,
                },
            }));

            const cursor = reset ? null : current.nextCursor;
            try {
                const response = await axios.get(`${API_BASE}/api/retrieve/file-chunks`, {
                    params: { fileId, limit: PAGE_SIZE, ...(cursor ? { cursor } : {}) },
                });
                const incoming = (response.data.chunks ?? []) as ParentChunkContent[];
                setTabStates((prev) => {
                    const state = prev[fileId] ?? createEmptyTabState();
                    const merged = reset ? incoming : [...state.chunks, ...incoming];
                    const deduped = Array.from(new Map(merged.map((c) => [c.parentId, c])).values());
                    return {
                        ...prev,
                        [fileId]: {
                            ...state,
                            chunks: deduped,
                            hasMore: Boolean(response.data.hasMore),
                            nextCursor: response.data.nextCursor ?? null,
                            isLoading: false,
                            isInitialized: true,
                            error: null,
                        },
                    };
                });
            } catch {
                const fileName = getFileNameById(fileId);
                setTabStates((prev) => ({
                    ...prev,
                    [fileId]: {
                        ...(prev[fileId] ?? createEmptyTabState()),
                        isLoading: false,
                        isInitialized: true,
                        error: `Failed to load content for ${fileName}.`,
                    },
                }));
            }
        },
        [tabStates, getFileNameById]
    );

    const getFullDocumentContent = useCallback(
        (fileId: string | null) => {
            if (!fileId) return "";
            return (tabStates[fileId] ?? createEmptyTabState()).chunks
                .map((c) => c.content).join("\n\n").trim();
        },
        [tabStates]
    );

    // ── Tab management ──

    const confirmDiscardUnsavedChanges = useCallback(() => {
        if (!editingFileId) return true;
        const original = getFullDocumentContent(editingFileId);
        const draft = editingDraftByFileId[editingFileId] ?? original;
        if (draft === original) { setEditingFileId(null); return true; }
        const ok = window.confirm("You have unsaved changes. Discard them?");
        if (ok) setEditingFileId(null);
        return ok;
    }, [editingDraftByFileId, editingFileId, getFullDocumentContent]);

    const openDocumentTab = useCallback(
        async (fileId: string) => {
            if (!confirmDiscardUnsavedChanges()) return;
            setOpenTabs((prev) => prev.includes(fileId) ? prev : [...prev, fileId]);
            setActiveTab(fileId);
            await loadFileChunks(fileId, true);
        },
        [confirmDiscardUnsavedChanges, loadFileChunks]
    );

    const closeDocumentTab = useCallback((fileId: string) => {
        if (!confirmDiscardUnsavedChanges()) return;
        setOpenTabs((prev) => {
            const idx = prev.indexOf(fileId);
            if (idx < 0) return prev;
            const next = prev.filter((id) => id !== fileId);
            setActiveTab((prevActive) => {
                if (prevActive !== fileId) return prevActive;
                return next.length === 0 ? null : next[Math.min(idx, next.length - 1)];
            });
            return next;
        });
    }, [confirmDiscardUnsavedChanges]);

    const setActiveDocumentTab = useCallback(
        async (fileId: string) => {
            if (!confirmDiscardUnsavedChanges()) return;
            setActiveTab(fileId);
            const state = tabStates[fileId];
            if (!state || !state.isInitialized) await loadFileChunks(fileId, false);
        },
        [confirmDiscardUnsavedChanges, loadFileChunks, tabStates]
    );

    const loadMoreActiveTab = useCallback(async () => {
        if (!activeTab) return;
        await loadFileChunks(activeTab, false);
    }, [activeTab, loadFileChunks]);

    const handleRefreshDocuments = useCallback(async () => {
        if (!confirmDiscardUnsavedChanges()) return;
        await fetchFiles();
    }, [confirmDiscardUnsavedChanges, fetchFiles]);

    const invalidateDocumentCache = useCallback(() => setIsDocsCached(false), []);

    const activeTabState = useMemo(
        () => (activeTab ? tabStates[activeTab] ?? createEmptyTabState() : null),
        [activeTab, tabStates]
    );

    // ── Manual document editing ──

    const editingDocumentContent = useMemo(() => {
        if (!editingFileId) return "";
        return editingDraftByFileId[editingFileId] ?? getFullDocumentContent(editingFileId);
    }, [editingDraftByFileId, editingFileId, getFullDocumentContent]);

    const isEditingActiveDocument = Boolean(activeTab && editingFileId && activeTab === editingFileId);
    const isSavingActiveDocument = Boolean(activeTab && savingFileId && activeTab === savingFileId);

    const isActiveDocumentDirty = useMemo(() => {
        if (!activeTab || !isEditingActiveDocument) return false;
        const original = getFullDocumentContent(activeTab);
        return (editingDraftByFileId[activeTab] ?? original) !== original;
    }, [activeTab, editingDraftByFileId, getFullDocumentContent, isEditingActiveDocument]);

    const startEditingActiveDocument = useCallback(() => {
        if (!activeTab || !activeTabState?.chunks.length) return;
        const fullContent = getFullDocumentContent(activeTab);
        setEditingFileId(activeTab);
        setEditingDraftByFileId((prev) => ({ ...prev, [activeTab]: prev[activeTab] ?? fullContent }));
        setSaveError(null);
    }, [activeTab, activeTabState?.chunks.length, getFullDocumentContent]);

    const setActiveEditingDocumentContent = useCallback(
        (nextContent: string) => {
            if (!editingFileId) return;
            setEditingDraftByFileId((prev) => ({ ...prev, [editingFileId]: nextContent }));
        },
        [editingFileId]
    );

    const cancelEditingActiveDocument = useCallback(() => {
        setEditingFileId(null);
        setSaveError(null);
    }, []);

    const saveEditingActiveDocument = useCallback(async () => {
        if (!activeTab || !editingFileId || activeTab !== editingFileId || savingFileId) return false;
        const fileName = getFileNameById(activeTab);
        if (!fileName) { setSaveError("Missing file name for this tab. Please refresh."); return false; }
        const original = getFullDocumentContent(activeTab);
        const draft = editingDraftByFileId[activeTab] ?? original;
        if (!draft.trim()) { setSaveError("Content cannot be empty."); return false; }
        if (draft === original) { setEditingFileId(null); return true; }

        setSavingFileId(activeTab);
        setSaveError(null);
        try {
            const response = await axios.put<UpdateFileResponse>(
                `${API_BASE}/api/modifications/update-file/${activeTab}`,
                { fileName, content: draft }
            );
            const updated = response.data;
            const localParentId = tabStates[activeTab]?.chunks[0]?.parentId ?? `local-${updated.fileId}`;

            setFiles((prev) => prev.map((f) =>
                f.fileId === updated.previousFileId
                    ? { ...f, fileId: updated.fileId, fileName: updated.fileName, previewTexts: buildPreviewText(updated.content) }
                    : f
            ));

            // Handle the (rare) case where the backend returns a new fileId
            if (updated.fileId !== updated.previousFileId) {
                setOpenTabs((prev) => prev.map((id) => id === updated.previousFileId ? updated.fileId : id));
                setActiveTab(updated.fileId);
                setTabStates((prev) => {
                    const { [updated.previousFileId]: _old, ...rest } = prev;
                    return {
                        ...rest,
                        [updated.fileId]: {
                            ...(_old ?? createEmptyTabState()),
                            chunks: [{ parentId: localParentId, content: updated.content, size: updated.size }],
                            hasMore: false, nextCursor: null, isLoading: false, isInitialized: true, error: null,
                        },
                    };
                });
            } else {
                setTabStates((prev) => ({
                    ...prev,
                    [activeTab]: {
                        ...(prev[activeTab] ?? createEmptyTabState()),
                        chunks: [{ parentId: localParentId, content: updated.content, size: updated.size }],
                        hasMore: false, nextCursor: null, isLoading: false, isInitialized: true, error: null,
                    },
                }));
            }

            setEditingFileId(null);
            // Clear stale draft so next edit session starts from the freshly saved content
            setEditingDraftByFileId((prev) => {
                const next = { ...prev };
                delete next[activeTab];
                return next;
            });
            return true;
        } catch {
            setSaveError("Failed to save document changes. Please try again.");
            return false;
        } finally {
            setSavingFileId(null);
        }
    }, [activeTab, editingDraftByFileId, editingFileId, getFileNameById, getFullDocumentContent, savingFileId, tabStates]);

    // ── Agent ──

    const clearAgentState = useCallback(() => {
        setAgentProposals([]);
        setAgentAcceptedMap(new Map());
        setAgentSavedIds(new Set());
        setAgentRejectedIds(new Set());
        setAgentSavingIds(new Set());
        setAgentError(null);
        setAgentIntention(null);
    }, []);

    const requestAgentEditPreview = useCallback(
        async (instruction: string, fileIds: string[] | null): Promise<RequestAgentResult> => {
            const trimmed = instruction.trim();
            if (!trimmed) return { ok: false, error: "Instruction cannot be empty." };
            if (isAgentGenerating) return { ok: false, error: "Agent is already running." };

            setIsAgentGenerating(true);
            setAgentError(null);
            setAgentProposals([]);
            setAgentAcceptedMap(new Map());
            setAgentSavedIds(new Set());
            setAgentRejectedIds(new Set());
            setAgentSavingIds(new Set());
            setAgentIntention(null);

            try {
                const response = await axios.post<AgentModifyResponse>(
                    `${API_BASE}/api/agent/modify`,
                    { instruction: trimmed, fileIds: fileIds && fileIds.length > 0 ? fileIds : null }
                );
                const { intention, proposals } = response.data;
                setAgentIntention(intention);
                setAgentProposals(proposals);
                const uniqueFiles = new Set(proposals.map((p) => p.fileId)).size;
                const summary = proposals.length > 0
                    ? `Agent found ${proposals.length} change(s) across ${uniqueFiles} file(s).`
                    : "Agent found no changes to make.";
                return { ok: true, summary };
            } catch {
                const error = "Agent failed to generate proposals. Please try again.";
                setAgentError(error);
                return { ok: false, error };
            } finally {
                setIsAgentGenerating(false);
            }
        },
        [isAgentGenerating]
    );

    // Accept: apply partial text replacement locally. Does NOT write to DB yet.
    const acceptAgentProposal = useCallback((proposal: AgentProposal) => {
        // Chunk must be loaded before accepting — we need the full surrounding content
        // to do a safe partial replace. Without it, save would write only the partial
        // proposed text to DB (Bug 1 variant).
        const hasChunk = tabStates[proposal.fileId]?.chunks.some(
            (c) => c.parentId === proposal.parentId
        );
        if (!hasChunk) {
            setSaveError(`Please open "${proposal.fileName}" and wait for it to load before accepting this proposal.`);
            return;
        }

        setAgentAcceptedMap((prev) => new Map(prev).set(proposal.parentId, proposal));
        setOpenTabs((prev) => prev.includes(proposal.fileId) ? prev : [...prev, proposal.fileId]);
        setActiveTab(proposal.fileId);

        setTabStates((prev) => {
            const state = prev[proposal.fileId] ?? createEmptyTabState();
            const updatedChunks = state.chunks.map((chunk) => {
                if (chunk.parentId !== proposal.parentId) return chunk;
                // If original text not found, leave the chunk untouched.
                if (!chunk.content.includes(proposal.original)) return chunk;
                const patched = chunk.content.replace(proposal.original, proposal.proposed);
                return { ...chunk, content: patched, size: patched.length };
            });
            return {
                ...prev,
                [proposal.fileId]: { ...state, chunks: updatedChunks, isInitialized: true, isLoading: false, error: null },
            };
        });
    }, [tabStates]);

    // Save: write accepted proposal to DB.
    const saveAgentProposal = useCallback(
        async (proposal: AgentProposal): Promise<boolean> => {
            setSaveError(null);
            setAgentSavingIds((prev) => new Set([...prev, proposal.parentId]));

            try {
                const existingChunk = tabStates[proposal.fileId]?.chunks.find(
                    (c) => c.parentId === proposal.parentId
                );

                // Chunk must be loaded — without full content we cannot safely write to DB
                if (!existingChunk) {
                    setSaveError(`Cannot save: open "${proposal.fileName}" and load its content first.`);
                    return false;
                }

                const resp = await axios.put<UpdateParentChunkResponse>(
                    `${API_BASE}/api/modifications/parent-chunks/${proposal.parentId}`,
                    { fileName: proposal.fileName, content: existingChunk.content }
                );

                const newParentId = resp.data.parentId;
                const oldParentId = resp.data.previousParentId;

                // Migrate acceptedMap: old→new for saved proposal, clear others for this file
                // (backend re-splits the entire file, making all other parentIds stale)
                setAgentAcceptedMap((prev) => {
                    const next = new Map(prev);
                    const old = next.get(oldParentId);
                    if (old) {
                        next.delete(oldParentId);
                        next.set(newParentId, { ...old, parentId: newParentId });
                    }
                    for (const [k, v] of next.entries()) {
                        if (v.fileId === proposal.fileId && v.parentId !== newParentId) next.delete(k);
                    }
                    return next;
                });

                // Migrate savedIds
                setAgentSavedIds((prev) => {
                    const next = new Set(prev);
                    next.delete(oldParentId);
                    next.add(newParentId);
                    return next;
                });

                // Migrate rejectedIds (old chunk gone; don't auto-add new as rejected)
                setAgentRejectedIds((prev) => {
                    const next = new Set(prev);
                    next.delete(oldParentId);
                    return next;
                });

                // Fix 2: single setAgentProposals call — migrate old→new then filter stale same-file proposals.
                // Fix 3: also collect stale parentIds to purge from saved/rejected sets.
                setAgentProposals((prev) => {
                    const migrated = prev.map((p) =>
                        p.parentId === oldParentId ? { ...p, parentId: newParentId } : p
                    );
                    const staleIds = migrated
                        .filter((p) => p.fileId === proposal.fileId && p.parentId !== newParentId)
                        .map((p) => p.parentId);

                    if (staleIds.length > 0) {
                        setAgentSavedIds((prev) => {
                            const next = new Set(prev);
                            staleIds.forEach((id) => next.delete(id));
                            return next;
                        });
                        setAgentRejectedIds((prev) => {
                            const next = new Set(prev);
                            staleIds.forEach((id) => next.delete(id));
                            return next;
                        });
                    }

                    return migrated.filter((p) => p.fileId !== proposal.fileId || p.parentId === newParentId);
                });

                // Clear savingIds for both old and new parentId
                setAgentSavingIds((prev) => {
                    const next = new Set(prev);
                    next.delete(oldParentId);
                    next.delete(newParentId);
                    return next;
                });

                await fetchFiles();
                await loadFileChunks(proposal.fileId, true);

                return true;
            } catch {
                setSaveError(`Failed to save changes for ${proposal.fileName}. Please try again.`);
                return false;
            } finally {
                setAgentSavingIds((prev) => {
                    const next = new Set(prev);
                    next.delete(proposal.parentId);
                    return next;
                });
            }
        },
        [tabStates, fetchFiles, loadFileChunks]
    );

    const rejectAgentProposal = useCallback((parentId: string) => {
        setAgentProposals((prev) => {
            const proposal = prev.find((p) => p.parentId === parentId);
            if (proposal) {
                setTabStates((tabPrev) => {
                    const state = tabPrev[proposal.fileId] ?? createEmptyTabState();
                    const reverted = state.chunks.map((chunk) => {
                        if (chunk.parentId !== parentId) return chunk;
                        // Reverse the partial patch: replace proposed text back to original.
                        // This mirrors acceptAgentProposal's replace(original, proposed),
                        // avoiding the bug where content = proposal.original truncates the whole chunk.
                        if (!chunk.content.includes(proposal.proposed)) return chunk;
                        const restored = chunk.content.replace(proposal.proposed, proposal.original);
                        return { ...chunk, content: restored, size: restored.length };
                    });
                    return { ...tabPrev, [proposal.fileId]: { ...state, chunks: reverted } };
                });
            }
            return prev;
        });
        setAgentAcceptedMap((prev) => {
            const next = new Map(prev);
            next.delete(parentId);
            return next;
        });
        setAgentRejectedIds((prev) => new Set([...prev, parentId]));
    }, []);

    return {
        files,
        isLoadingFiles,
        fileListError,
        openTabs,
        activeTab,
        activeTabState,
        fetchFiles,
        handleRefreshDocuments,
        invalidateDocumentCache,
        openDocumentTab,
        closeDocumentTab,
        setActiveDocumentTab,
        loadMoreActiveTab,
        saveError,
        editingDocumentContent,
        isEditingActiveDocument,
        isSavingActiveDocument,
        isActiveDocumentDirty,
        startEditingActiveDocument,
        setActiveEditingDocumentContent,
        cancelEditingActiveDocument,
        saveEditingActiveDocument,
        getFileNameById,
        getFileIdByName,
        isAgentGenerating,
        agentProposals,
        agentAcceptedMap,
        agentSavedIds,
        agentRejectedIds,
        agentSavingIds,
        agentError,
        agentIntention,
        requestAgentEditPreview,
        acceptAgentProposal,
        saveAgentProposal,
        rejectAgentProposal,
        clearAgentState,
    };
}