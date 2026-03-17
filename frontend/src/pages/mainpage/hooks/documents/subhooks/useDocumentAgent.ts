import { useCallback, useState, type Dispatch, type SetStateAction } from "react";
import type { AgentProposal, FileContentState, FilesState, HighlightedSelection, ParentChunkContent } from "../../../types";
import { requestAgentModify, requestSelectionPreview, type AgentModifyResponse } from "../api/documentsApi";
import { buildChunkRanges } from "../utils/chunkText";
import { findNearestOccurrence } from "../utils/editText";
import { remapAcceptedAgentOffsets } from "../state/transitions";

export type RequestAgentResult = {
    ok: boolean;
    summary?: string;
    error?: string;
};

type UseDocumentAgentParams = {
    editingFileId: string | null;
    editingDraftByFileId: Record<string, string>;
    setEditingFileId: Dispatch<SetStateAction<string | null>>;
    setEditingDraftByFileId: Dispatch<SetStateAction<Record<string, string>>>;
    setSaveError: Dispatch<SetStateAction<string | null>>;
    getContentStateById: (fileId: string) => FileContentState;
    getEditorBaselineContent: (fileId: string | null) => string;
    loadFileChunks: (fileId: string, reset?: boolean) => Promise<ParentChunkContent[]>;
    setFilesState: Dispatch<SetStateAction<FilesState>>;
};

// Converts backend proposal shape into the frontend's internal proposal model.
function normalizeIncomingProposal(
    proposal: AgentModifyResponse["proposals"][number]
): AgentProposal {
    return {
        fileId: proposal.fileId,
        fileName: proposal.fileName,
        parentId: proposal.parentId,
        original: proposal.original,
        proposed: proposal.proposed,
        source: proposal.source,
        selectionStart: proposal.selectionStart,
        selectionEnd: proposal.selectionEnd,
    };
}

// Manages AI proposal requests and proposal application/revert flows.
export function useDocumentAgent({
    editingFileId,
    editingDraftByFileId,
    setEditingFileId,
    setEditingDraftByFileId,
    setSaveError,
    getContentStateById,
    getEditorBaselineContent,
    loadFileChunks,
    setFilesState,
}: UseDocumentAgentParams) {
    const [isAgentGenerating, setIsAgentGenerating] = useState(false);
    const [agentProposals, setAgentProposals] = useState<AgentProposal[]>([]);
    const [agentAcceptedMap, setAgentAcceptedMap] = useState<Map<string, AgentProposal>>(new Map());
    const [agentRejectedIds, setAgentRejectedIds] = useState<Set<string>>(new Set());
    const [agentError, setAgentError] = useState<string | null>(null);
    const [agentIntention, setAgentIntention] = useState<string | null>(null);

    // Clears all proposal UI state for a fresh request cycle.
    const clearAgentState = useCallback(() => {
        setAgentProposals([]);
        setAgentAcceptedMap(new Map());
        setAgentRejectedIds(new Set());
        setAgentError(null);
        setAgentIntention(null);
    }, []);

    const getKnownParentIdsForFile = useCallback(
        (fileId: string) =>
            new Set<string>([
                ...getContentStateById(fileId).chunks.map((chunk) => chunk.parentId),
                ...agentProposals.filter((proposal) => proposal.fileId === fileId).map((proposal) => proposal.parentId),
                ...Array.from(agentAcceptedMap.values())
                    .filter((proposal) => proposal.fileId === fileId)
                    .map((proposal) => proposal.parentId),
            ]),
        [agentAcceptedMap, agentProposals, getContentStateById]
    );

    // Clears proposal state tied to a single file while preserving other files.
    const clearAgentStateForFile = useCallback(
        (fileId: string) => {
            const knownParentIds = getKnownParentIdsForFile(fileId);
            setAgentProposals((prev) => prev.filter((proposal) => proposal.fileId !== fileId));
            setAgentAcceptedMap((prev) => {
                const next = new Map(prev);
                for (const [parentId, proposal] of next.entries()) {
                    if (proposal.fileId === fileId) next.delete(parentId);
                }
                return next;
            });
            setAgentRejectedIds((prev) =>
                new Set([...prev].filter((parentId) => !knownParentIds.has(parentId)))
            );
        },
        [getKnownParentIdsForFile]
    );

    const requestAgentEditPreview = useCallback(
        async (instruction: string, fileIds: string[] | null): Promise<RequestAgentResult> => {
            const trimmed = instruction.trim();
            if (!trimmed) return { ok: false, error: "Instruction cannot be empty." };
            if (isAgentGenerating) return { ok: false, error: "Agent is already running." };
            if (editingFileId) {
                return {
                    ok: false,
                    error: "Finish the current edit session by saving or cancelling before requesting new AI changes.",
                };
            }

            setIsAgentGenerating(true);
            setAgentError(null);
            setAgentProposals([]);
            setAgentAcceptedMap(new Map());
            setAgentRejectedIds(new Set());
            setAgentIntention(null);

            try {
                const { intention, proposals } = await requestAgentModify(trimmed, fileIds);
                const mapped = proposals.map(normalizeIncomingProposal);
                setAgentIntention(intention);
                setAgentProposals(mapped);
                const uniqueFiles = new Set(mapped.map((p) => p.fileId)).size;
                const summary = mapped.length > 0
                    ? `Agent found ${mapped.length} change(s) across ${uniqueFiles} file(s).`
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
        [editingFileId, isAgentGenerating]
    );

    // Requests an edit proposal for an explicit highlighted text region.
    const requestSelectionEditPreview = useCallback(
        async (instruction: string, selection: HighlightedSelection): Promise<RequestAgentResult> => {
            const trimmed = instruction.trim();
            if (!trimmed) return { ok: false, error: "Instruction cannot be empty." };
            if (isAgentGenerating) return { ok: false, error: "Agent is already running." };
            if (editingFileId) {
                return {
                    ok: false,
                    error: "Finish the current edit session by saving or cancelling before requesting new AI changes.",
                };
            }

            setFilesState((prev) => ({
                ...prev,
                openTabIds: prev.openTabIds.includes(selection.fileId)
                    ? prev.openTabIds
                    : [...prev.openTabIds, selection.fileId],
                activeFileId: selection.fileId,
            }));
            setIsAgentGenerating(true);
            setAgentError(null);
            setAgentProposals([]);
            setAgentAcceptedMap(new Map());
            setAgentRejectedIds(new Set());
            setAgentIntention("selection");

            try {
                const preview = await requestSelectionPreview(trimmed, selection);
                setAgentProposals([
                    {
                        fileId: preview.fileId,
                        fileName: preview.fileName,
                        parentId: preview.selectionId,
                        original: preview.selectedText,
                        proposed: preview.proposedText,
                        source: "selection",
                        selectionStart: preview.startOffset,
                        selectionEnd: preview.endOffset,
                    },
                ]);

                const summary = preview.proposedText.trim().endsWith("?")
                    ? "The editor asked for clarification. Review the proposal in the edit panel."
                    : "Selection edit preview generated. Review the proposal in the edit panel.";
                return { ok: true, summary };
            } catch {
                const requestError = "Selected-text edit failed. Please try again.";
                setAgentError(requestError);
                return { ok: false, error: requestError };
            } finally {
                setIsAgentGenerating(false);
            }
        },
        [editingFileId, isAgentGenerating, setFilesState]
    );

    // Applies one proposal into the current draft and tracks positional offsets.
    const acceptAgentProposal = useCallback(async (proposal: AgentProposal) => {
        if (agentAcceptedMap.has(proposal.parentId)) return;
        if (editingFileId && editingFileId !== proposal.fileId) {
            setSaveError("Save or cancel the current edit session before applying a proposal in another file.");
            return;
        }

        const isSelectionProposal =
            proposal.source === "selection" &&
            proposal.selectionStart !== undefined &&
            proposal.selectionEnd !== undefined;

        let baselineOffset: number;
        if (isSelectionProposal) {
            const currentContentState = getContentStateById(proposal.fileId);
            if (!currentContentState.chunks.length) {
                await loadFileChunks(proposal.fileId, true);
            }

            const baselineContent = getEditorBaselineContent(proposal.fileId);
            if (!baselineContent) {
                setSaveError(`Cannot load "${proposal.fileName}" content. Please refresh and try again.`);
                return;
            }

            const selectionStart = proposal.selectionStart;
            const selectionEnd = proposal.selectionEnd;
            if (selectionStart === undefined || selectionEnd === undefined) {
                setSaveError("The highlighted selection metadata is incomplete.");
                return;
            }
            if (
                selectionStart < 0 ||
                selectionEnd <= selectionStart ||
                selectionEnd > baselineContent.length
            ) {
                setSaveError("The highlighted selection is out of range for the current document.");
                return;
            }

            baselineOffset = selectionStart;
            const selectedFromBaseline = baselineContent.slice(selectionStart, selectionEnd);
            if (selectedFromBaseline !== proposal.original) {
                // Selection text may shift after refresh; pick nearest compatible occurrence.
                const fallbackOffset = findNearestOccurrence(
                    baselineContent,
                    proposal.original,
                    selectionStart
                );
                if (fallbackOffset === -1) {
                    setSaveError("The highlighted text no longer matches the current document content.");
                    return;
                }
                baselineOffset = fallbackOffset;
            }
        } else {
            let targetChunk = getContentStateById(proposal.fileId).chunks.find(
                (c) => c.parentId === proposal.parentId
            );
            if (!targetChunk) {
                const freshChunks = await loadFileChunks(proposal.fileId, true);
                targetChunk = freshChunks.find((c) => c.parentId === proposal.parentId);
            }
            if (!targetChunk) {
                setSaveError(`Cannot load "${proposal.fileName}" content. Please refresh and try again.`);
                return;
            }

            // Compute absolute offset using chunk boundaries + offset inside the chunk.
            const state = getContentStateById(proposal.fileId);
            const { ranges } = buildChunkRanges(state.chunks);
            const targetRange = ranges.find((range) => range.parentId === proposal.parentId);
            if (!targetRange) {
                setSaveError("The target chunk for this proposal is no longer available.");
                return;
            }

            const chunkOffset = targetChunk.content.indexOf(proposal.original);
            if (chunkOffset === -1) {
                setSaveError(`Original text was not found for "${proposal.fileName}". The proposal may be stale.`);
                return;
            }
            baselineOffset = targetRange.start + chunkOffset;
        }

        // Shift target offset by previously accepted edits in the same file.
        const baseDraft = editingDraftByFileId[proposal.fileId] ?? getEditorBaselineContent(proposal.fileId);
        const priorDelta = Array.from(agentAcceptedMap.values())
            .filter(
                (entry) =>
                    entry.fileId === proposal.fileId &&
                    entry.parentId !== proposal.parentId &&
                    typeof entry.patchBaselineOffset === "number" &&
                    entry.patchBaselineOffset < baselineOffset
            )
            .reduce((sum, entry) => sum + (entry.proposed.length - entry.original.length), 0);

        let draftOffset = baselineOffset + priorDelta;
        if (baseDraft.slice(draftOffset, draftOffset + proposal.original.length) !== proposal.original) {
            draftOffset = findNearestOccurrence(baseDraft, proposal.original, draftOffset);
        }
        if (draftOffset === -1) {
            setSaveError("Failed to apply proposal because the current draft no longer matches the proposal source text.");
            return;
        }

        const patchedDraft =
            baseDraft.slice(0, draftOffset) +
            proposal.proposed +
            baseDraft.slice(draftOffset + proposal.original.length);

        setFilesState((prev) => ({
            ...prev,
            openTabIds: prev.openTabIds.includes(proposal.fileId)
                ? prev.openTabIds
                : [...prev.openTabIds, proposal.fileId],
            activeFileId: proposal.fileId,
        }));
        setEditingFileId(proposal.fileId);
        setEditingDraftByFileId((prev) => ({ ...prev, [proposal.fileId]: patchedDraft }));
        setAgentAcceptedMap((prev) => {
            if (prev.has(proposal.parentId)) return prev;
            const next = remapAcceptedAgentOffsets(
                prev,
                proposal.parentId,
                proposal.fileId,
                draftOffset,
                proposal.proposed.length - proposal.original.length
            );
            next.set(proposal.parentId, {
                ...proposal,
                patchOffset: draftOffset,
                patchBaselineOffset: baselineOffset,
            });
            return next;
        });
        setAgentRejectedIds((prev) => {
            const next = new Set(prev);
            next.delete(proposal.parentId);
            return next;
        });
        setSaveError(null);
    }, [
        agentAcceptedMap,
        editingDraftByFileId,
        editingFileId,
        getContentStateById,
        getEditorBaselineContent,
        loadFileChunks,
        setEditingDraftByFileId,
        setEditingFileId,
        setFilesState,
        setSaveError,
    ]);

    // Reverts an accepted proposal or marks an unseen proposal as rejected.
    const rejectAgentProposal = useCallback((parentId: string) => {
        const proposal = agentProposals.find((entry) => entry.parentId === parentId);
        if (!proposal) return;
        if (editingFileId && editingFileId !== proposal.fileId) {
            setSaveError("Save or cancel the current edit session before rejecting a proposal in another file.");
            return;
        }

        const acceptedEntry = agentAcceptedMap.get(parentId);
        if (!acceptedEntry || acceptedEntry.patchOffset === undefined) {
            setAgentRejectedIds((prev) => new Set([...prev, parentId]));
            return;
        }

        const currentDraft = editingDraftByFileId[proposal.fileId] ?? getEditorBaselineContent(proposal.fileId);
        let patchOffset = acceptedEntry.patchOffset;
        const expectedEnd = patchOffset + proposal.proposed.length;
        if (currentDraft.slice(patchOffset, expectedEnd) !== proposal.proposed) {
            // If draft shifted, recover by nearest proposal text occurrence.
            patchOffset = findNearestOccurrence(currentDraft, proposal.proposed, patchOffset);
        }
        if (patchOffset === -1) {
            setSaveError("Failed to discard the accepted proposal because the draft has diverged.");
            return;
        }

        const restoredDraft =
            currentDraft.slice(0, patchOffset) +
            proposal.original +
            currentDraft.slice(patchOffset + proposal.proposed.length);

        setEditingFileId(proposal.fileId);
        setEditingDraftByFileId((prev) => ({ ...prev, [proposal.fileId]: restoredDraft }));
        setAgentAcceptedMap((prev) => {
            const next = new Map(prev);
            next.delete(parentId);
            return remapAcceptedAgentOffsets(
                next,
                parentId,
                proposal.fileId,
                patchOffset,
                proposal.original.length - proposal.proposed.length
            );
        });
        setAgentRejectedIds((prev) => new Set([...prev, parentId]));
        setSaveError(null);
    }, [
        agentAcceptedMap,
        agentProposals,
        editingDraftByFileId,
        editingFileId,
        getEditorBaselineContent,
        setEditingDraftByFileId,
        setEditingFileId,
        setSaveError,
    ]);

    return {
        isAgentGenerating,
        agentProposals,
        agentAcceptedMap,
        agentRejectedIds,
        agentError,
        agentIntention,
        clearAgentState,
        clearAgentStateForFile,
        requestAgentEditPreview,
        requestSelectionEditPreview,
        acceptAgentProposal,
        rejectAgentProposal,
    };
}
