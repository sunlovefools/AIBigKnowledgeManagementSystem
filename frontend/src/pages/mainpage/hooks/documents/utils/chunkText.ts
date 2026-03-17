import type { ParentChunkContent } from "../../../types";

// Absolute text offsets for one parent chunk in the concatenated document string.
export type ChunkRange = {
    parentId: string;
    start: number;
    end: number;
    content: string;
};

// Produces compact one-line preview text for sidebar rendering.
export function buildPreviewText(content: string): string {
    return content.replace(/\s+/g, " ").trim().slice(0, 160);
}

// Builds a single document string and index ranges used by edit/agent offset math.
export function buildChunkRanges(chunks: ParentChunkContent[]): { fullText: string; ranges: ChunkRange[] } {
    if (!chunks.length) return { fullText: "", ranges: [] };

    let cursor = 0;
    const ranges: ChunkRange[] = [];

    chunks.forEach((chunk, index) => {
        const start = cursor;
        const end = start + chunk.content.length;
        ranges.push({ parentId: chunk.parentId, start, end, content: chunk.content });
        cursor = end;
        if (index < chunks.length - 1) cursor += 2;
    });

    return {
        fullText: chunks.map((chunk) => chunk.content).join("\n\n"),
        ranges,
    };
}
