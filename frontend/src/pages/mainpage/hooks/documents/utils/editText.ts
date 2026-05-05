import { normalizeMarkdownForEditor } from "../../../utils/markdownEditor";
import type { ChunkRange } from "./chunkText";

// Finds the minimal single replace window between original and draft strings.
export function computeSingleReplaceEdit(
    original: string,
    draft: string
): { start: number; end: number; replacement: string } | null {
    if (original === draft) return null;

    let left = 0;
    while (left < original.length && left < draft.length && original[left] === draft[left]) {
        left += 1;
    }

    let right = 0;
    while (
        right < original.length - left &&
        right < draft.length - left &&
        original[original.length - 1 - right] === draft[draft.length - 1 - right]
    ) {
        right += 1;
    }

    return {
        start: left,
        end: original.length - right,
        replacement: draft.slice(left, draft.length - right),
    };
}

// Returns chunk ranges touched by an edit, including boundary-only insert/delete cases.
export function findTouchedRangesForEdit(
    ranges: ChunkRange[],
    edit: { start: number; end: number }
): ChunkRange[] {
    const overlapping = ranges.filter((range) => range.start < edit.end && edit.start < range.end);
    if (overlapping.length > 0) return overlapping;

    const isInsertion = edit.start === edit.end;

    if (isInsertion) {
        const endedHere = ranges.filter((range) => range.end === edit.start);
        const startedHere = ranges.filter((range) => range.start === edit.start);

        if (endedHere.length > 0 && startedHere.length > 0) {
            return [endedHere[endedHere.length - 1]];
        }
        if (endedHere.length > 0) return [endedHere[endedHere.length - 1]];
        if (startedHere.length > 0) return [startedHere[0]];
    } else {
        const boundaryTouching = ranges.filter(
            (range) => range.end === edit.start || range.start === edit.end
        );
        if (boundaryTouching.length > 0) return boundaryTouching;
    }

    let previous: ChunkRange | null = null;
    for (const range of ranges) {
        if (range.end <= edit.start) previous = range;
        else break;
    }
    const next = ranges.find((range) => range.start >= edit.end) ?? null;
    if (previous && next && previous.parentId !== next.parentId) {
        const isInsideGap = previous.end <= edit.start && edit.end <= next.start;
        if (isInsideGap) {
            if (isInsertion) {
                const distanceToPrevious = edit.start - previous.end;
                const distanceToNext = next.start - edit.end;
                return distanceToPrevious <= distanceToNext ? [previous] : [next];
            }
            return [previous, next];
        }
    }

    return [];
}

// Collects parent IDs that require boundary-aware rechunking for a given edit.
export function collectBoundaryTouchedParentIds(
    ranges: ChunkRange[],
    edit: { start: number; end: number },
    originalLength: number
): string[] {
    if (!ranges.length) return [];

    const touched = new Set<string>();
    const boundaryPositions = new Set<number>();

    ranges.forEach((range) => {
        boundaryPositions.add(range.start);
        boundaryPositions.add(range.end);
    });

    const isEndOfDocumentInsertion = edit.start === originalLength && edit.end === originalLength;
    if (isEndOfDocumentInsertion) {
        const previous = ranges[ranges.length - 1];
        if (previous) touched.add(previous.parentId);
        return Array.from(touched);
    }

    const startHitsBoundary = boundaryPositions.has(edit.start);
    const endHitsBoundary = boundaryPositions.has(edit.end);
    const isInsertion = edit.start === edit.end;
    const crossesInternalBoundary = Array.from(boundaryPositions).some(
        (position) => position > edit.start && position < edit.end
    );
    const overlapsAnyChunk = isInsertion
        ? ranges.some((range) => range.start < edit.start && edit.start < range.end)
        : ranges.some((range) => range.start < edit.end && edit.start < range.end);

    let previous: ChunkRange | null = null;
    for (const range of ranges) {
        if (range.end <= edit.start) previous = range;
        else break;
    }
    const next = ranges.find((range) => range.start >= edit.end) ?? null;
    const insideGap =
        previous !== null &&
        next !== null &&
        previous.end <= edit.start &&
        edit.end <= next.start &&
        !overlapsAnyChunk;

    if (!startHitsBoundary && !endHitsBoundary && !insideGap && !crossesInternalBoundary) {
        return [];
    }

    if (crossesInternalBoundary) {
        ranges
            .filter((range) => range.end > edit.start && range.start < edit.end)
            .forEach((range) => touched.add(range.parentId));
    }

    if (previous) touched.add(previous.parentId);
    if (next) touched.add(next.parentId);

    if (touched.size === 0) {
        ranges
            .filter(
                (range) =>
                    range.start === edit.start ||
                    range.end === edit.start ||
                    range.start === edit.end ||
                    range.end === edit.end
            )
            .forEach((range) => touched.add(range.parentId));
    }

    return Array.from(touched);
}

// Raw HTML is saved through full-file update because markdown chunking assumptions break.
export function containsRawHtmlMarkup(text: string): boolean {
    return /<\/?[a-z][^>]*>/i.test(text);
}

// Finds the closest match to expectedOffset when the exact location has shifted.
export function findNearestOccurrence(
    haystack: string,
    needle: string,
    expectedOffset?: number
): number {
    if (!needle) return -1;
    const first = haystack.indexOf(needle);
    if (first === -1) return -1;
    if (expectedOffset === undefined) return first;

    let best = first;
    let bestDistance = Math.abs(first - expectedOffset);
    let cursor = first;
    while (cursor !== -1) {
        const next = haystack.indexOf(needle, cursor + 1);
        if (next === -1) break;
        const distance = Math.abs(next - expectedOffset);
        if (distance < bestDistance) {
            best = next;
            bestDistance = distance;
        }
        cursor = next;
    }
    return best;
}

// Compares normalized markdown to ignore non-meaningful editor differences.
export function hasMeaningfulEditorChange(original: string, draft: string): boolean {
    return normalizeMarkdownForEditor(original) !== normalizeMarkdownForEditor(draft);
}
