import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import type { ChatScope, UserCollectionSummary } from "../types";

type ScopePickerProps = {
    scope: ChatScope;
    collections: UserCollectionSummary[];
    disabled?: boolean;
    isLoadingCollections: boolean;
    collectionError: string | null;
    onScopeChange: (scope: ChatScope) => void;
    triggerPrefix?: string;
    instruction?: string;
    includeAllCollections?: boolean;
    allCollectionsMeta?: string;
};

function getScopeLabel(scope: ChatScope, collections: UserCollectionSummary[]): string {
    if (scope.type === "all_collections") return "All collections";
    return scope.collectionName
        || collections.find((collection) => collection.collectionId === scope.collectionId)?.name
        || "Selected collection";
}

export default function ScopePicker({
    scope,
    collections,
    disabled = false,
    isLoadingCollections,
    collectionError,
    onScopeChange,
    triggerPrefix = "Search",
    instruction = "Select the collection you want to search at",
    includeAllCollections = true,
    allCollectionsMeta = "Search everything you own",
}: ScopePickerProps) {
    const pickerRef = useRef<HTMLDivElement | null>(null);
    const popoverRef = useRef<HTMLDivElement | null>(null);
    const [isOpen, setIsOpen] = useState(false);
    const [popoverStyle, setPopoverStyle] = useState<CSSProperties>({});
    const [hasPopoverPosition, setHasPopoverPosition] = useState(false);

    const scopeLabel = getScopeLabel(scope, collections);

    const updatePopoverPosition = useCallback(() => {
        const trigger = pickerRef.current;
        if (!trigger) {
            setHasPopoverPosition(false);
            return;
        }

        const rect = trigger.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) {
            setHasPopoverPosition(false);
            return;
        }

        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;
        const measuredHeight = popoverRef.current
            ? Math.max(popoverRef.current.scrollHeight, popoverRef.current.offsetHeight)
            : 0;
        const estimatedRows = collections.length + (includeAllCollections ? 1 : 0);
        const estimatedHeight = Math.min(320, 82 + Math.min(estimatedRows, 5) * 56);
        const naturalHeight = measuredHeight > 0 ? measuredHeight : estimatedHeight;
        const width = Math.min(360, Math.max(240, Math.min(viewportWidth - 24, Math.max(rect.width, 280))));
        const left = Math.min(Math.max(12, rect.left), Math.max(12, viewportWidth - width - 12));
        const spaceAbove = rect.top - 12;
        const spaceBelow = viewportHeight - rect.bottom - 12;
        const shouldOpenAbove = spaceAbove >= naturalHeight + 8 || spaceAbove > spaceBelow;
        const availableSpace = Math.max(120, Math.min(shouldOpenAbove ? spaceAbove - 8 : spaceBelow - 8, viewportHeight - 24));
        const availableHeight = Math.min(naturalHeight, availableSpace);
        const isHeightConstrained = naturalHeight > availableHeight;
        const top = shouldOpenAbove
            ? Math.max(12, rect.top - availableHeight - 8)
            : Math.min(rect.bottom + 8, viewportHeight - availableHeight - 12);

        setPopoverStyle({
            position: "fixed",
            left,
            top,
            width,
            maxHeight: availableHeight,
            height: isHeightConstrained ? availableHeight : undefined,
        });
        setHasPopoverPosition(true);
    }, [collections.length, includeAllCollections]);

    useLayoutEffect(() => {
        if (!isOpen) return;
        updatePopoverPosition();
        const frameId = window.requestAnimationFrame(updatePopoverPosition);
        return () => window.cancelAnimationFrame(frameId);
    }, [isOpen, updatePopoverPosition, collections.length, collectionError, includeAllCollections, isLoadingCollections]);

    useEffect(() => {
        if (!isOpen) {
            setHasPopoverPosition(false);
            return;
        }
        updatePopoverPosition();

        const handlePointerDown = (event: MouseEvent) => {
            const target = event.target as Node | null;
            if (
                !target
                || pickerRef.current?.contains(target)
                || popoverRef.current?.contains(target)
            ) return;
            setIsOpen(false);
        };

        const handleEscape = (event: globalThis.KeyboardEvent) => {
            if (event.key !== "Escape") return;
            setIsOpen(false);
        };

        document.addEventListener("mousedown", handlePointerDown);
        document.addEventListener("keydown", handleEscape);
        window.addEventListener("resize", updatePopoverPosition);
        window.addEventListener("scroll", updatePopoverPosition, true);
        return () => {
            document.removeEventListener("mousedown", handlePointerDown);
            document.removeEventListener("keydown", handleEscape);
            window.removeEventListener("resize", updatePopoverPosition);
            window.removeEventListener("scroll", updatePopoverPosition, true);
        };
    }, [isOpen, updatePopoverPosition]);

    const selectAllCollections = () => {
        onScopeChange({ type: "all_collections" });
        setIsOpen(false);
    };

    const selectCollection = (collection: UserCollectionSummary) => {
        onScopeChange({
            type: "collection",
            collectionId: collection.collectionId,
            collectionName: collection.name,
        });
        setIsOpen(false);
    };

    return (
        <div className="scope-picker" ref={pickerRef}>
            <button
                className={`scope-picker-trigger ${isOpen ? "open" : ""}`}
                type="button"
                onClick={() => setIsOpen((current) => !current)}
                disabled={disabled}
                aria-haspopup="dialog"
                aria-expanded={isOpen}
                title="Choose search scope"
            >
                <span className="scope-picker-trigger-kicker">{triggerPrefix}</span>
                <span className="scope-picker-trigger-label">{scopeLabel}</span>
                <span className="scope-picker-trigger-chevron" aria-hidden="true">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="6 9 12 15 18 9" />
                    </svg>
                </span>
            </button>

            {isOpen && createPortal(
                <div
                    ref={popoverRef}
                    className="scope-picker-popover"
                    role="dialog"
                    aria-label="Search scope"
                    style={{
                        ...popoverStyle,
                        visibility: hasPopoverPosition ? "visible" : "hidden",
                    }}
                >
                    <div className="scope-picker-heading">
                        {instruction}
                    </div>

                    {collectionError && <div className="scope-picker-status error">{collectionError}</div>}

                    <div className="scope-picker-list">
                        {includeAllCollections && (
                            <button
                                className={`scope-picker-row ${scope.type === "all_collections" ? "active" : ""}`}
                                type="button"
                                onClick={selectAllCollections}
                            >
                                <span className="scope-picker-row-main">
                                    <span className="scope-picker-row-title">All collections</span>
                                    <span className="scope-picker-row-meta">{allCollectionsMeta}</span>
                                </span>
                            </button>
                        )}

                        {isLoadingCollections ? (
                            <div className="scope-picker-status">Loading collections...</div>
                        ) : collections.length === 0 ? (
                            <div className="scope-picker-status">No collections yet.</div>
                        ) : (
                            collections.map((collection) => (
                                <button
                                    key={collection.collectionId}
                                    className={`scope-picker-row ${scope.type === "collection" && scope.collectionId === collection.collectionId ? "active" : ""}`}
                                    type="button"
                                    onClick={() => selectCollection(collection)}
                                >
                                    <span className="scope-picker-row-main">
                                        <span className="scope-picker-row-title">{collection.name}</span>
                                        <span className="scope-picker-row-meta">{collection.fileCount} file(s)</span>
                                    </span>
                                </button>
                            ))
                        )}
                    </div>
                </div>,
                document.body
            )}
        </div>
    );
}
