import {
    useCallback,
    useMemo,
    useRef,
    useState,
    type ChangeEvent,
    type DragEvent,
    type ReactNode,
} from "react";
import { apiClient } from "../auth/apiClient";
import {
    FILE_INPUT_ACCEPT,
    isSupportedUploadFile,
    resolveUploadContentType,
} from "../pages/mainpage/utils/uploadFormats";
import {
    UploadQueueContext,
    type IngestUploadResponse,
    type IngestUploadJobAcceptedResponse,
    type IngestUploadJobStatusResponse,
    type UploadCompletionEvent,
    type UploadQueueContextValue,
    type UploadQueueItem,
    type UploadQueueStatus,
    type UploadQueueTarget,
} from "./uploadQueueState";
import "./UploadQueue.css";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
const INGEST_JOB_POLL_INTERVAL_MS = 1200;

function createUploadId(): string {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
        return `upload-${crypto.randomUUID()}`;
    }
    return `upload-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function normalizeTarget(target?: UploadQueueTarget): Required<UploadQueueTarget> {
    return {
        collectionId: target?.collectionId ?? null,
        collectionName: target?.collectionName ?? null,
    };
}

function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
        value /= 1024;
        unitIndex += 1;
    }
    return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

function getUploadErrorMessage(error: unknown): string {
    if (error && typeof error === "object") {
        const response = (error as { response?: { data?: { detail?: unknown } } }).response;
        const detail = response?.data?.detail;
        if (typeof detail === "string" && detail.trim()) {
            return detail.trim();
        }
    }
    if (error instanceof Error && error.message.trim()) {
        return error.message.trim();
    }
    return "Upload failed.";
}

function waitForPollDelay(): Promise<void> {
    return new Promise((resolve) => {
        window.setTimeout(resolve, INGEST_JOB_POLL_INTERVAL_MS);
    });
}

function isFileDrag(event: DragEvent<HTMLElement>): boolean {
    return Array.from(event.dataTransfer.types).includes("Files");
}

function statusLabel(status: UploadQueueStatus): string {
    if (status === "queued") return "Queued";
    if (status === "reading") return "Reading";
    if (status === "uploading") return "Uploading";
    if (status === "processing") return "Processing";
    if (status === "completed") return "Uploaded";
    return "Failed";
}

export function UploadQueueProvider({ children }: { children: ReactNode }) {
    const [items, setItems] = useState<UploadQueueItem[]>([]);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [target, setTarget] = useState<Required<UploadQueueTarget>>(normalizeTarget());
    const [isDraggingOverModal, setIsDraggingOverModal] = useState(false);
    const fileInputRef = useRef<HTMLInputElement | null>(null);
    const itemsRef = useRef<UploadQueueItem[]>([]);
    const targetRef = useRef<Required<UploadQueueTarget>>(normalizeTarget());
    const isProcessingRef = useRef(false);
    const subscribersRef = useRef(new Set<(event: UploadCompletionEvent) => void>());

    const commitItems = useCallback((updater: (current: UploadQueueItem[]) => UploadQueueItem[]) => {
        const nextItems = updater(itemsRef.current);
        itemsRef.current = nextItems;
        setItems(nextItems);
    }, []);

    const notifyCompletion = useCallback((event: UploadCompletionEvent) => {
        subscribersRef.current.forEach((handler) => handler(event));
    }, []);

    const updateTarget = useCallback((nextTarget?: UploadQueueTarget) => {
        if (!nextTarget) return;
        const normalized = normalizeTarget(nextTarget);
        targetRef.current = normalized;
        setTarget(normalized);
    }, []);

    const openModal = useCallback((nextTarget?: UploadQueueTarget) => {
        updateTarget(nextTarget);
        setIsModalOpen(true);
    }, [updateTarget]);

    const closeModal = useCallback(() => {
        setIsModalOpen(false);
        setIsDraggingOverModal(false);
    }, []);

    const readFileAsBase64 = useCallback(
        (item: UploadQueueItem): Promise<string> =>
            new Promise((resolve, reject) => {
                const reader = new FileReader();

                reader.onprogress = (event) => {
                    if (!event.lengthComputable || event.total <= 0) return;
                    const readRatio = Math.min(event.loaded / event.total, 1);
                    commitItems((current) =>
                        current.map((entry) =>
                            entry.id === item.id
                                ? {
                                    ...entry,
                                    status: "reading",
                                    progress: Math.max(2, Math.round(readRatio * 22)),
                                    phaseLabel: "Reading file",
                                }
                                : entry
                        )
                    );
                };

                reader.onload = () => {
                    const result = String(reader.result || "");
                    const commaIndex = result.indexOf(",");
                    resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : result);
                };

                reader.onerror = () => reject(new Error("Could not read this file."));

                commitItems((current) =>
                    current.map((entry) =>
                        entry.id === item.id
                            ? { ...entry, status: "reading", progress: 2, phaseLabel: "Reading file", error: null }
                            : entry
                    )
                );
                reader.readAsDataURL(item.file);
            }),
        [commitItems]
    );

    const pollIngestJob = useCallback(
        async (item: UploadQueueItem, jobId: string): Promise<IngestUploadResponse> => {
            while (true) {
                const response = await apiClient.get<IngestUploadJobStatusResponse>(
                    `${API_BASE}/ingest/upload-jobs/${jobId}`
                );
                const job = response.data;

                if (job.status === "succeeded") {
                    if (!job.result) {
                        throw new Error("Upload completed without a result payload.");
                    }
                    return job.result;
                }

                if (job.status === "failed") {
                    throw new Error(job.error || "Upload processing failed.");
                }

                commitItems((current) =>
                    current.map((entry) =>
                        entry.id === item.id
                            ? {
                                ...entry,
                                status: "processing",
                                progress: Math.max(entry.progress, 88),
                                phaseLabel: job.status === "queued" ? "Queued for processing" : "Processing",
                            }
                            : entry
                    )
                );
                await waitForPollDelay();
            }
        },
        [commitItems]
    );

    const processItem = useCallback(
        async (item: UploadQueueItem) => {
            if (!isSupportedUploadFile(item.fileName)) {
                const failedItem: UploadQueueItem = {
                    ...item,
                    status: "failed",
                    progress: 100,
                    phaseLabel: "Unsupported file",
                    error: "Upload PDF, DOC, DOCX, TXT, PPTX, or XLSX files.",
                };
                commitItems((current) => current.map((entry) => (entry.id === item.id ? failedItem : entry)));
                notifyCompletion({ item: failedItem, ok: false });
                return;
            }

            try {
                const base64Data = await readFileAsBase64(item);
                commitItems((current) =>
                    current.map((entry) =>
                        entry.id === item.id
                            ? { ...entry, status: "uploading", progress: 25, phaseLabel: "Uploading" }
                            : entry
                    )
                );

                const acceptedResponse = await apiClient.post<IngestUploadJobAcceptedResponse>(
                    `${API_BASE}/ingest/upload-jobs`,
                    {
                        fileName: item.fileName,
                        contentType: resolveUploadContentType(item.file),
                        data: base64Data,
                        ...(item.collectionId ? { collectionId: item.collectionId } : {}),
                    },
                    {
                        onUploadProgress: (event) => {
                            if (!event.total || event.total <= 0) {
                                commitItems((current) =>
                                    current.map((entry) =>
                                        entry.id === item.id
                                            ? {
                                                ...entry,
                                                status: "uploading",
                                                progress: Math.max(entry.progress, 35),
                                                phaseLabel: "Uploading",
                                            }
                                            : entry
                                    )
                                );
                                return;
                            }

                            const uploadRatio = Math.min(event.loaded / event.total, 1);
                            const uploadProgress = Math.round(25 + uploadRatio * 45);
                            commitItems((current) =>
                                current.map((entry) =>
                                    entry.id === item.id
                                        ? {
                                            ...entry,
                                            status: uploadRatio >= 1 ? "processing" : "uploading",
                                            progress: uploadRatio >= 1 ? 88 : uploadProgress,
                                            phaseLabel: uploadRatio >= 1 ? "Processing" : "Uploading",
                                        }
                                        : entry
                                )
                            );
                        },
                    }
                );
                commitItems((current) =>
                    current.map((entry) =>
                        entry.id === item.id
                            ? {
                                ...entry,
                                status: "processing",
                                progress: Math.max(entry.progress, 88),
                                phaseLabel: "Queued for processing",
                            }
                            : entry
                    )
                );

                const ingestResult = await pollIngestJob(item, acceptedResponse.data.jobId);

                const completedItem: UploadQueueItem = {
                    ...item,
                    status: "completed",
                    progress: 100,
                    phaseLabel: "Uploaded",
                    error: null,
                    response: ingestResult,
                };
                commitItems((current) => current.map((entry) => (entry.id === item.id ? completedItem : entry)));
                notifyCompletion({ item: completedItem, ok: true });
            } catch (error) {
                const failedItem: UploadQueueItem = {
                    ...item,
                    status: "failed",
                    progress: 100,
                    phaseLabel: "Failed",
                    error: getUploadErrorMessage(error),
                };
                commitItems((current) => current.map((entry) => (entry.id === item.id ? failedItem : entry)));
                notifyCompletion({ item: failedItem, ok: false });
            }
        },
        [commitItems, notifyCompletion, pollIngestJob, readFileAsBase64]
    );

    const processQueue = useCallback(async () => {
        if (isProcessingRef.current) return;
        isProcessingRef.current = true;

        try {
            while (true) {
                const nextItem = itemsRef.current.find((item) => item.status === "queued");
                if (!nextItem) break;
                await processItem(nextItem);
            }
        } finally {
            isProcessingRef.current = false;
        }
    }, [processItem]);

    const enqueueFiles = useCallback(
        (incomingFiles: FileList | File[], nextTarget?: UploadQueueTarget) => {
            const normalizedTarget = nextTarget ? normalizeTarget(nextTarget) : targetRef.current;
            targetRef.current = normalizedTarget;
            setTarget(normalizedTarget);

            const files = Array.from(incomingFiles);
            if (files.length === 0) {
                setIsModalOpen(true);
                return;
            }

            const nextItems = files.map<UploadQueueItem>((file) => {
                const isSupported = isSupportedUploadFile(file.name);
                return {
                    id: createUploadId(),
                    file,
                    fileName: file.name,
                    size: file.size,
                    collectionId: normalizedTarget.collectionId,
                    collectionName: normalizedTarget.collectionName,
                    status: isSupported ? "queued" : "failed",
                    progress: isSupported ? 0 : 100,
                    phaseLabel: isSupported ? "Queued" : "Unsupported file",
                    error: isSupported ? null : "Upload PDF, DOC, DOCX, TXT, PPTX, or XLSX files.",
                    response: null,
                };
            });

            commitItems((current) => [...current, ...nextItems]);
            setIsModalOpen(true);
            void processQueue();
        },
        [commitItems, processQueue]
    );

    const openFilePicker = useCallback(
        (nextTarget?: UploadQueueTarget) => {
            openModal(nextTarget);
            window.setTimeout(() => fileInputRef.current?.click(), 0);
        },
        [openModal]
    );

    const retryItem = useCallback(
        (itemId: string) => {
            commitItems((current) =>
                current.map((item) => {
                    if (item.id !== itemId || item.status !== "failed") return item;
                    if (!isSupportedUploadFile(item.fileName)) return item;
                    return {
                        ...item,
                        status: "queued",
                        progress: 0,
                        phaseLabel: "Queued",
                        error: null,
                        response: null,
                    };
                })
            );
            void processQueue();
        },
        [commitItems, processQueue]
    );

    const clearCompleted = useCallback(() => {
        commitItems((current) => current.filter((item) => item.status !== "completed"));
    }, [commitItems]);

    const subscribeToCompletions = useCallback((handler: (event: UploadCompletionEvent) => void) => {
        subscribersRef.current.add(handler);
        return () => {
            subscribersRef.current.delete(handler);
        };
    }, []);

    const handleInputChange = (event: ChangeEvent<HTMLInputElement>) => {
        if (event.target.files) {
            enqueueFiles(event.target.files, targetRef.current);
        }
        event.target.value = "";
    };

    const handleModalDragOver = (event: DragEvent<HTMLElement>) => {
        if (!isFileDrag(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        setIsDraggingOverModal(true);
    };

    const handleModalDragLeave = (event: DragEvent<HTMLElement>) => {
        const nextTarget = event.relatedTarget as Node | null;
        if (!nextTarget || !event.currentTarget.contains(nextTarget)) {
            setIsDraggingOverModal(false);
        }
    };

    const handleModalDrop = (event: DragEvent<HTMLElement>) => {
        if (!isFileDrag(event)) return;
        event.preventDefault();
        setIsDraggingOverModal(false);
        enqueueFiles(event.dataTransfer.files, targetRef.current);
    };

    const hasActiveUploads = items.some((item) =>
        item.status === "queued" || item.status === "reading" || item.status === "uploading" || item.status === "processing"
    );
    const completedCount = items.filter((item) => item.status === "completed").length;
    const failedCount = items.filter((item) => item.status === "failed").length;
    const totalCount = items.length;
    const activeTargetLabel = `Collection: ${target.collectionName || "current collection"}`;

    const contextValue = useMemo<UploadQueueContextValue>(
        () => ({
            items,
            isModalOpen,
            hasActiveUploads,
            enqueueFiles,
            openModal,
            closeModal,
            openFilePicker,
            retryItem,
            clearCompleted,
            subscribeToCompletions,
        }),
        [
            clearCompleted,
            closeModal,
            enqueueFiles,
            hasActiveUploads,
            isModalOpen,
            items,
            openFilePicker,
            openModal,
            retryItem,
            subscribeToCompletions,
        ]
    );

    return (
        <UploadQueueContext.Provider value={contextValue}>
            {children}
            <input
                ref={fileInputRef}
                className="upload-queue-hidden-input"
                type="file"
                accept={FILE_INPUT_ACCEPT}
                multiple
                onChange={handleInputChange}
            />

            {isModalOpen && (
                <div className="upload-queue-overlay" role="presentation" onMouseDown={closeModal}>
                    <section
                        className="upload-queue-modal"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="upload-queue-title"
                        onMouseDown={(event) => event.stopPropagation()}
                    >
                        <div className="upload-queue-header">
                            <div>
                                <div className="upload-queue-eyebrow">Knowledge base</div>
                                <h2 id="upload-queue-title">Upload files</h2>
                                <p>{activeTargetLabel}</p>
                            </div>
                            <button className="upload-queue-close" type="button" onClick={closeModal} aria-label="Close upload queue">
                                x
                            </button>
                        </div>

                        <div
                            className={`upload-queue-dropzone ${isDraggingOverModal ? "dragging" : ""}`}
                            onDragOver={handleModalDragOver}
                            onDragLeave={handleModalDragLeave}
                            onDrop={handleModalDrop}
                        >
                            <div className="upload-queue-drop-icon" aria-hidden="true">
                                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M12 16V4" />
                                    <path d="m7 9 5-5 5 5" />
                                    <path d="M20 16.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-2.5" />
                                </svg>
                            </div>
                            <div>
                                <div className="upload-queue-drop-title">Drop files here</div>
                                <div className="upload-queue-drop-copy">PDF, DOC, DOCX, TXT, PPTX, XLSX</div>
                            </div>
                            <button className="upload-queue-add-btn" type="button" onClick={() => fileInputRef.current?.click()}>
                                Upload files
                            </button>
                        </div>

                        <div className="upload-queue-summary">
                            <span>{totalCount} queued</span>
                            <span>{completedCount} uploaded</span>
                            {failedCount > 0 && <span className="failed">{failedCount} failed</span>}
                        </div>

                        <div className="upload-queue-list" role="list" aria-label="Upload queue">
                            {items.length === 0 ? (
                                <div className="upload-queue-empty">No files queued.</div>
                            ) : (
                                items.map((item) => {
                                    const canRetry = item.status === "failed" && isSupportedUploadFile(item.fileName);
                                    return (
                                        <div key={item.id} className={`upload-queue-item ${item.status}`} role="listitem">
                                            <div className="upload-queue-item-main">
                                                <div className="upload-queue-file-row">
                                                    <span className="upload-queue-file-name" title={item.fileName}>
                                                        {item.fileName}
                                                    </span>
                                                    <span className={`upload-queue-status ${item.status}`}>
                                                        {statusLabel(item.status)}
                                                    </span>
                                                </div>
                                                <div className="upload-queue-file-meta">
                                                    <span>{formatBytes(item.size)}</span>
                                                    <span>{item.collectionName || "Default collection"}</span>
                                                </div>
                                                <div className={`upload-queue-progress ${item.status === "processing" ? "indeterminate" : ""}`}>
                                                    <span style={{ width: `${Math.max(0, Math.min(item.progress, 100))}%` }} />
                                                </div>
                                                <div className={`upload-queue-phase ${item.status}`}>
                                                    {item.error || item.phaseLabel}
                                                </div>
                                            </div>
                                            {canRetry && (
                                                <button className="upload-queue-retry" type="button" onClick={() => retryItem(item.id)}>
                                                    Retry
                                                </button>
                                            )}
                                        </div>
                                    );
                                })
                            )}
                        </div>

                        <div className="upload-queue-footer">
                            <button
                                className="upload-queue-secondary"
                                type="button"
                                onClick={clearCompleted}
                                disabled={completedCount === 0}
                            >
                                Clear uploaded
                            </button>
                        </div>
                    </section>
                </div>
            )}

            {!isModalOpen && items.length > 0 && (
                <button className="upload-queue-dock" type="button" onClick={() => openModal()} aria-label="Open upload queue">
                    <span className={`upload-queue-dock-dot ${hasActiveUploads ? "active" : failedCount > 0 ? "failed" : "completed"}`} />
                    <span className="upload-queue-dock-main">
                        <span className="upload-queue-dock-title">
                            {hasActiveUploads ? "Uploads running" : failedCount > 0 ? "Uploads need attention" : "Uploads complete"}
                        </span>
                        <span className="upload-queue-dock-meta">
                            {completedCount}/{totalCount} uploaded{failedCount > 0 ? `, ${failedCount} failed` : ""}
                        </span>
                    </span>
                </button>
            )}
        </UploadQueueContext.Provider>
    );
}
