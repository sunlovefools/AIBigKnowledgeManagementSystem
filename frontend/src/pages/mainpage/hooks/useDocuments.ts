import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

    // Ref mirror of agentAcceptedMap — lets rejectAgentProposal (deps=[]) read the
    // latest accepted entries (including patchOffset) without a stale closure. (F01)
    const agentAcceptedMapRef = useRef(agentAcceptedMap);
    useEffect(() => { agentAcceptedMapRef.current = agentAcceptedMap; }, [agentAcceptedMap]);


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
        async (fileId: string, reset = false): Promise<ParentChunkContent[]> => {
            const current = tabStates[fileId] ?? createEmptyTabState();
            // When reset=true (called by acceptAgentProposal), always fetch fresh data
            // even if a load is already in progress — the in-progress load may be
            // for a different page or stale state.
            if (!reset && current.isLoading) return current.chunks;
            if (!reset && current.isInitialized && !current.hasMore) return current.chunks;

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
                const existing = reset ? [] : (tabStates[fileId]?.chunks ?? []);
                const merged = reset ? incoming : [...existing, ...incoming];
                const deduped = Array.from(new Map(merged.map((c) => [c.parentId, c])).values());
                setTabStates((prev) => {
                    const state = prev[fileId] ?? createEmptyTabState();
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
                // Return the freshly loaded chunks directly — callers like
                // acceptAgentProposal need these immediately without waiting
                // for React to re-render and update tabStates.
                return deduped;
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
                return [];
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
    // Auto-loads the file's chunks if not yet in tabStates, so the user never
    // has to manually open a file before clicking Accept.
    const acceptAgentProposal = useCallback(async (proposal: AgentProposal) => {
        // If the chunk isn't loaded yet, trigger loading now and wait for it.
        let targetChunk = tabStates[proposal.fileId]?.chunks.find(
            (c) => c.parentId === proposal.parentId
        );
        if (!targetChunk) {
            // Open the tab and trigger loading — this is the first-time path.
            setOpenTabs((prev) => prev.includes(proposal.fileId) ? prev : [...prev, proposal.fileId]);
            setActiveTab(proposal.fileId);
            // loadFileChunks returns the chunks directly, so we don't need to
            // wait for React state to update before reading them.
            const freshChunks = await loadFileChunks(proposal.fileId, true);
            targetChunk = freshChunks.find((c) => c.parentId === proposal.parentId);
        }
        if (!targetChunk) {
            setSaveError(`无法加载"${proposal.fileName}"的内容，请确认文件存在后重试。`);
            return;
        }

        // B01: use indexOf to find exact position instead of .replace().
        // .replace(a, b) only changes the first occurrence silently; indexOf lets us
        // verify existence, count duplicates, and record the precise offset for revert.
        const offset = targetChunk.content.indexOf(proposal.original);
        if (offset === -1) {
            // Original text no longer exists in the chunk (e.g. stale proposal after
            // another edit). Reject silently rather than writing corrupt content.
            setSaveError(`原文未在文档中找到（可能是过期的 proposal），已跳过。`);
            return;
        }

        // Warn when the original appears more than once so the user is aware only
        // the first occurrence will be patched.
        const matchCount = targetChunk.content.split(proposal.original).length - 1;
        if (matchCount > 1) {
            console.warn(
                `[B01] "${proposal.original.slice(0, 60)}…" appears ${matchCount} times ` +
                `in chunk ${proposal.parentId}. Only the first occurrence (offset=${offset}) ` +
                `will be replaced.`
            );
        }

        // Store offset alongside the proposal so rejectAgentProposal can restore
        // the exact position without guessing. (F01 fix)
        setAgentAcceptedMap((prev) =>
            new Map(prev).set(proposal.parentId, { ...proposal, patchOffset: offset })
        );
        // Ensure tab is open and active (no-op if already set by auto-load above).
        setOpenTabs((prev) => prev.includes(proposal.fileId) ? prev : [...prev, proposal.fileId]);
        setActiveTab(proposal.fileId);

        setTabStates((prev) => {
            const state = prev[proposal.fileId] ?? createEmptyTabState();
            const updatedChunks = state.chunks.map((chunk) => {
                if (chunk.parentId !== proposal.parentId) return chunk;
                const patched =
                    chunk.content.slice(0, offset) +
                    proposal.proposed +
                    chunk.content.slice(offset + proposal.original.length);
                return { ...chunk, content: patched, size: patched.length };
            });
            return {
                ...prev,
                [proposal.fileId]: { ...state, chunks: updatedChunks, isInitialized: true, isLoading: false, error: null },
            };
        });
    }, [tabStates, loadFileChunks]);

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

                        // F01: use the offset recorded at accept time for a positionally
                        // exact revert. Without it, .replace(proposed, original) would
                        // silently restore the wrong occurrence if `proposed` text appears
                        // elsewhere in the chunk.
                        const acceptedEntry = agentAcceptedMapRef.current.get(parentId);
                        const offset = acceptedEntry?.patchOffset;

                        if (offset !== undefined) {
                            const proposedEnd = offset + proposal.proposed.length;
                            // Verify the proposed text is still at the recorded position
                            // (it should be, unless some other edit moved it).
                            if (chunk.content.slice(offset, proposedEnd) === proposal.proposed) {
                                const restored =
                                    chunk.content.slice(0, offset) +
                                    proposal.original +
                                    chunk.content.slice(proposedEnd);
                                return { ...chunk, content: restored, size: restored.length };
                            }
                            // Position check failed — fall through to indexOf fallback.
                            console.warn(
                                `[F01] Expected "${proposal.proposed.slice(0, 40)}…" at offset ${offset} ` +
                                `but found "${chunk.content.slice(offset, offset + 40)}…". ` +
                                `Falling back to indexOf.`
                            );
                        }

                        // Fallback: find by indexOf (covers proposals accepted before this
                        // fix was deployed, or edge cases where offset is unavailable).
                        const pos = chunk.content.indexOf(proposal.proposed);
                        if (pos === -1) return chunk;
                        const restored =
                            chunk.content.slice(0, pos) +
                            proposal.original +
                            chunk.content.slice(pos + proposal.proposed.length);
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
    }, []); // agentAcceptedMapRef is a stable ref — no dependency needed

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