import { createContext, useContext } from "react";

export type UploadQueueStatus =
    | "queued"
    | "reading"
    | "uploading"
    | "processing"
    | "completed"
    | "failed";

export type UploadQueueTarget = {
    collectionId?: string | null;
    collectionName?: string | null;
};

export type IngestUploadResponse = {
    status: string;
    message: string;
    file_name: string;
    parent_chunks: number;
    child_chunks: number;
    warnings: string[];
};

export type IngestUploadJobAcceptedResponse = {
    jobId: string;
    status: "queued";
    fileName: string;
    collectionId: string;
};

export type IngestUploadJobStatusResponse = {
    jobId: string;
    status: "queued" | "running" | "succeeded" | "failed" | "canceled" | string;
    fileName: string;
    collectionId: string;
    collectionName?: string | null;
    result?: IngestUploadResponse | null;
    error?: string | null;
    submittedAt: string;
    startedAt?: string | null;
    finishedAt?: string | null;
};

export type UploadQueueItem = {
    id: string;
    file: File;
    fileName: string;
    size: number;
    collectionId: string | null;
    collectionName: string | null;
    status: UploadQueueStatus;
    progress: number;
    phaseLabel: string;
    error: string | null;
    response: IngestUploadResponse | null;
    jobId?: string | null;
};

export type UploadCompletionEvent = {
    item: UploadQueueItem;
    ok: boolean;
};

export type UploadQueueContextValue = {
    items: UploadQueueItem[];
    isModalOpen: boolean;
    hasActiveUploads: boolean;
    enqueueFiles: (files: FileList | File[], target?: UploadQueueTarget) => void;
    openModal: (target?: UploadQueueTarget) => void;
    closeModal: () => void;
    openFilePicker: (target?: UploadQueueTarget) => void;
    retryItem: (itemId: string) => void;
    removeItem: (itemId: string) => void;
    clearCompleted: () => void;
    subscribeToCompletions: (handler: (event: UploadCompletionEvent) => void) => () => void;
};

export const UploadQueueContext = createContext<UploadQueueContextValue | null>(null);

export function useUploadQueue(): UploadQueueContextValue {
    const context = useContext(UploadQueueContext);
    if (!context) {
        throw new Error("useUploadQueue must be used within UploadQueueProvider.");
    }
    return context;
}
