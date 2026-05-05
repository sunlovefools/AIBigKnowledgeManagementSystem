# Documents Hook Module

This folder contains the document-management domain for the main page modification panel.
It handles file listing, tab state, chunk loading, editing/saving flows, and AI proposal workflows.

## Folder Structure

- `useDocuments.ts`: Facade hook that composes file, editing, and agent subhooks into one public API for the UI.
- `api/documentsApi.ts`: Backend API client functions and response types for file retrieval, updates, deletion, and AI preview endpoints.
- `subhooks/useDocumentFiles.ts`: File list state, open/active tab state, chunk pagination/loading, caching, and related selectors.
- `subhooks/useDocumentEditing.ts`: Edit session lifecycle (start/edit/cancel/save), dirty checks, save strategy selection, and local state remapping.
- `subhooks/useDocumentAgent.ts`: AI proposal request flow, selection preview flow, accept/reject logic, and proposal offset tracking.
- `state/factories.ts`: Constructors for normalized default state objects and entry conversion helpers.
- `state/transitions.ts`: Pure state transition helpers for sidebar sync, tab operations, chunk patching, deletion, and ID/offset remapping.
- `utils/chunkText.ts`: Chunk range/index helpers and sidebar preview text generation.
- `utils/editText.ts`: Text diff/edit-boundary helpers for deciding fast chunk updates vs full-file saves.

## Detailed File Reference

### 1) `useDocuments.ts`
Main orchestrator hook used by components.

What it does:
- Initializes and wires:
  - `useDocumentFiles` (file/chunk state)
  - `useDocumentEditing` (editing + saving)
  - `useDocumentAgent` (AI proposals)
- Exposes derived helpers:
  - `getFullDocumentContent`: joins all parent chunks into one document string.
  - `getEditorBaselineContent`: current baseline used for diffing/proposals.
- Guards tab switching/closing/refresh with unsaved-change confirmation.
- Implements `deleteFile` flow:
  - Calls backend delete endpoint.
  - Removes file/tab/chunk async state locally.
  - Clears draft and AI proposal state for the deleted file.

Key returned API categories:
- File and tab state (`files`, `openTabs`, `activeTab`, async loading info)
- File actions (`fetchFiles`, `deleteFile`, tab open/close/switch, load-more)
- Edit actions (`start`, `set draft`, `cancel`, `save`, dirty/saving state)
- Agent actions (`request preview`, `accept/reject`, clear proposal state)

### 2) `api/documentsApi.ts`
Network layer for this module.

What it contains:
- `API_BASE` normalization (removes trailing slash from `VITE_API_BASE`).
- Shared pagination constant: `PAGE_SIZE = 7`.
- Response type contracts:
  - `UpdateParentChunkResponse`
  - `BatchUpdateParentChunksResponse`
  - `UpdateFileResponse`
  - `DeleteFileResponse`
  - `AgentModifyResponse`
  - `SelectionEditPreviewResponse`
- API functions:
  - `getAllPreviewFiles()`
  - `getFileChunks(fileId, cursor)`
  - `deleteKnowledgeFile(fileId)`
  - `updateFileContent(fileId, fileName, content)`
  - `batchUpdateParentChunks(payload)`
  - `requestAgentModify(instruction, fileIds)`
  - `requestSelectionPreview(instruction, selection)`
  - `getAxiosErrorDetail(error)` for extracting backend detail strings.

### 3) `subhooks/useDocumentFiles.ts`
Owns the file browser and chunk-loading domain state.

What it manages:
- Root document state (`filesState`) with normalized index (`byId`) and tab IDs.
- Per-file async state (`chunkAsyncByFileId`) for content loading status/errors.
- Fetch/loading flags:
  - `isLoadingFiles`
  - `fileListError`
  - document cache flag (`isDocsCached`)
  - deletion-in-progress marker (`deletingFileId`)

Core behaviors:
- `fetchFiles`: gets sidebar file metadata and syncs local indexes.
- Lazy initial fetch: only when modification panel opens.
- `loadFileChunks(fileId, reset)`: paginated chunk loading with deduplication by `parentId`.
- Derived selectors for active tab state and helper lookups (`getFileNameById`, `getFileIdByName`, etc.).
- `invalidateDocumentCache` to force a future refresh.

### 4) `subhooks/useDocumentEditing.ts`
Implements the editing lifecycle and persistence logic.

State inside this hook:
- `editingFileId`
- `editingDraftByFileId` (draft content per file)
- `savingFileId`
- `saveError`

Main responsibilities:
- Start/cancel editing and maintain per-file drafts.
- Detect dirty state using normalized markdown comparison.
- Confirm discard for unsaved edits.
- Save logic (`saveEditingActiveDocument`) chooses between:
  - Fast chunk updates (`batchUpdateParentChunks` with `fast_updates`)
  - Boundary-aware rechunking (`batchUpdateParentChunks` with `boundary_rechunk`)
  - Full-file fallback (`updateFileContent`) when needed
- Applies server responses to local state (including file ID remap after full-file updates).
- Clears proposal state for a file after successful save/cancel.

### 5) `subhooks/useDocumentAgent.ts`
Handles AI proposal generation and proposal application/reversion.

State inside this hook:
- `isAgentGenerating`
- `agentProposals`
- `agentAcceptedMap`
- `agentRejectedIds`
- `agentError`
- `agentIntention`

Core operations:
- `requestAgentEditPreview(instruction, fileIds)` for multi-file proposals.
- `requestSelectionEditPreview(instruction, selection)` for highlighted-selection rewrite.
- `acceptAgentProposal(proposal)`:
  - Resolves offset in baseline text.
  - Applies prior accepted-delta adjustments.
  - Patches active draft and tracks offsets for future remaps.
- `rejectAgentProposal(parentId)`:
  - Reverts accepted proposal text when possible.
  - Or marks unseen proposal as rejected.
- File-scoped and global clearing (`clearAgentStateForFile`, `clearAgentState`).

### 6) `state/factories.ts`
Pure constructors and converters.

Functions:
- `createEmptyContentState()`
- `createEmptyContentAsyncState()`
- `createFileEntry(summary, contentState?)`
- `createEmptyFilesState()`
- `toSidebarFileSummary(entry)`

Purpose:
- Keeps default state shapes centralized and consistent.

### 7) `state/transitions.ts`
Pure state-transition utilities used by hooks.

Key transitions:
- Sidebar/index sync:
  - `replaceFilesFromSidebarSummaries`
  - `syncChunkAsyncIndex`
- Tab/file transitions:
  - `openTabState`
  - `closeTabState`
  - `removeFileFromState`
- Chunk loading/content transitions:
  - `markFileChunkLoading`
  - `patchChunkContent`
- Save/remap transitions:
  - `remapAfterFullFileUpdate`
  - `remapAcceptedAgentOffsets`

Purpose:
- Keep complex state updates deterministic and testable.

### 8) `utils/chunkText.ts`
Helpers for chunk-to-document mapping.

Exports:
- `ChunkRange` type: absolute start/end offsets per parent chunk.
- `buildPreviewText(content)`: one-line preview text for sidebar.
- `buildChunkRanges(chunks)`: builds full document text plus chunk ranges used by edit/agent offset math.

### 9) `utils/editText.ts`
Text-edit analysis helpers supporting save strategy and robust offseting.

Exports:
- `computeSingleReplaceEdit(original, draft)`
- `findTouchedRangesForEdit(ranges, edit)`
- `collectBoundaryTouchedParentIds(ranges, edit, originalLength)`
- `containsRawHtmlMarkup(text)`
- `findNearestOccurrence(haystack, needle, expectedOffset?)`
- `hasMeaningfulEditorChange(original, draft)`

Purpose:
- Detects precise edit windows and chunk boundary effects.
- Supports safe fallbacks when draft/proposal offsets drift.

## How These Files Work Together

1. UI calls `useDocuments`.
2. `useDocumentFiles` loads sidebar files and chunk content on demand.
3. `useDocumentEditing` opens draft mode and saves using fast or full update paths.
4. `useDocumentAgent` requests AI suggestions and applies/reverts them against the draft.
5. `state/*` modules provide pure transitions and default-state factories.
6. `utils/*` modules provide text/chunk math for deterministic updates.
7. `api/documentsApi.ts` is the boundary to backend endpoints.
