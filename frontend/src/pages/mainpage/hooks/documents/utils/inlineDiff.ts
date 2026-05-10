import type { InlineDiffToken, ProposalHunk } from "../../../types";

// Splits text into tokens while preserving whitespace as separate tokens.
// "A  B C" -> ["A", "  ", "B", " ", "C"]
function tokenizePreservingWhitespace(text: string): string[] {
    return text.match(/\s+|\S+/g) ?? [];
}

// Merges adjacent tokens of the same type into a single token.
function mergeAdjacent(tokens: InlineDiffToken[]): InlineDiffToken[] {
    if (!tokens.length) return [];
    const merged: InlineDiffToken[] = [{ ...tokens[0] }]; // Start with the first token
    for (let index = 1; index < tokens.length; index += 1) {
        const current = tokens[index];
        const last = merged[merged.length - 1];
        // If the current token is the same type as the last merged token, merge their text.
        if (last.type === current.type) {
            last.text += current.text;
            continue;
        }
        merged.push({ ...current });
    }
    return merged;
}

type PositionedDiffToken = InlineDiffToken & {
    originalStart: number;
    originalEnd: number;
    proposedStart: number;
    proposedEnd: number;
};

function pushPositionedToken(
    tokens: PositionedDiffToken[],
    token: PositionedDiffToken
) {
    const last = tokens[tokens.length - 1];
    if (
        last &&
        last.type === token.type &&
        last.originalEnd === token.originalStart &&
        last.proposedEnd === token.proposedStart
    ) {
        last.text += token.text;
        last.originalEnd = token.originalEnd;
        last.proposedEnd = token.proposedEnd;
        return;
    }
    tokens.push(token);
}

function buildPositionedDiffTokens(original: string, proposed: string): PositionedDiffToken[] {
    if (original === proposed) {
        return [{
            type: "equal",
            text: original,
            originalStart: 0,
            originalEnd: original.length,
            proposedStart: 0,
            proposedEnd: proposed.length,
        }];
    }

    const before = tokenizePreservingWhitespace(original);
    const after = tokenizePreservingWhitespace(proposed);
    const beforeOffsets: number[] = [];
    const afterOffsets: number[] = [];
    let beforeCursor = 0;
    let afterCursor = 0;

    for (const token of before) {
        beforeOffsets.push(beforeCursor);
        beforeCursor += token.length;
    }
    for (const token of after) {
        afterOffsets.push(afterCursor);
        afterCursor += token.length;
    }

    const beforeLength = before.length;
    const afterLength = after.length;

    const dp: Uint32Array[] = Array.from({ length: beforeLength + 1 }, () => new Uint32Array(afterLength + 1));

    // Fill the DP table for LCS lengths.
    for (let i = beforeLength - 1; i >= 0; i -= 1) {
        for (let j = afterLength - 1; j >= 0; j -= 1) {
            dp[i][j] = before[i] === after[j]
                ? dp[i + 1][j + 1] + 1
                : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }

    const rawTokens: PositionedDiffToken[] = [];
    let i = 0;
    let j = 0;
    while (i < beforeLength && j < afterLength) {
        if (before[i] === after[j]) {
            pushPositionedToken(rawTokens, {
                type: "equal",
                text: before[i],
                originalStart: beforeOffsets[i],
                originalEnd: beforeOffsets[i] + before[i].length,
                proposedStart: afterOffsets[j],
                proposedEnd: afterOffsets[j] + after[j].length,
            });
            i += 1;
            j += 1;
            continue;
        }

        if (dp[i + 1][j] >= dp[i][j + 1]) {
            pushPositionedToken(rawTokens, {
                type: "del",
                text: before[i],
                originalStart: beforeOffsets[i],
                originalEnd: beforeOffsets[i] + before[i].length,
                proposedStart: j < afterOffsets.length ? afterOffsets[j] : proposed.length,
                proposedEnd: j < afterOffsets.length ? afterOffsets[j] : proposed.length,
            });
            i += 1;
        } else {
            pushPositionedToken(rawTokens, {
                type: "add",
                text: after[j],
                originalStart: i < beforeOffsets.length ? beforeOffsets[i] : original.length,
                originalEnd: i < beforeOffsets.length ? beforeOffsets[i] : original.length,
                proposedStart: afterOffsets[j],
                proposedEnd: afterOffsets[j] + after[j].length,
            });
            j += 1;
        }
    }

    while (i < beforeLength) {
        pushPositionedToken(rawTokens, {
            type: "del",
            text: before[i],
            originalStart: beforeOffsets[i],
            originalEnd: beforeOffsets[i] + before[i].length,
            proposedStart: proposed.length,
            proposedEnd: proposed.length,
        });
        i += 1;
    }
    while (j < afterLength) {
        pushPositionedToken(rawTokens, {
            type: "add",
            text: after[j],
            originalStart: original.length,
            originalEnd: original.length,
            proposedStart: afterOffsets[j],
            proposedEnd: afterOffsets[j] + after[j].length,
        });
        j += 1;
    }

    return rawTokens;
}

// Computes a whitespace-preserving token diff using an LCS walk.
export function buildInlineDiffTokens(original: string, proposed: string): InlineDiffToken[] {
    const positioned = buildPositionedDiffTokens(original, proposed);
    return mergeAdjacent(positioned.map(({ type, text }) => ({ type, text })));
}

function trimEqualPrefixSuffix(original: string, proposed: string) {
    let prefix = 0;
    while (
        prefix < original.length &&
        prefix < proposed.length &&
        original[prefix] === proposed[prefix]
    ) {
        prefix += 1;
    }

    let suffix = 0;
    while (
        suffix < original.length - prefix &&
        suffix < proposed.length - prefix &&
        original[original.length - 1 - suffix] === proposed[proposed.length - 1 - suffix]
    ) {
        suffix += 1;
    }

    return {
        originalStart: prefix,
        originalEnd: original.length - suffix,
        proposedStart: prefix,
        proposedEnd: proposed.length - suffix,
    };
}

function hunkType(originalText: string, proposedText: string): ProposalHunk["type"] {
    if (!originalText) return "insert";
    if (!proposedText) return "delete";
    return "replace";
}

function isReviewTokenCharacter(char: string): boolean {
    return /[A-Za-z0-9_%]/.test(char);
}

function expandReplaceHunkToToken(original: string, proposed: string, hunk: ProposalHunk): ProposalHunk {
    if (hunk.type !== "replace" || !hunk.originalText || !hunk.proposedText) return hunk;

    let originalStart = hunk.originalStart;
    let originalEnd = hunk.originalEnd;
    let proposedStart = hunk.proposedStart;
    let proposedEnd = hunk.proposedEnd;

    while (originalStart > 0 && isReviewTokenCharacter(original[originalStart - 1])) {
        originalStart -= 1;
    }
    while (originalEnd < original.length && isReviewTokenCharacter(original[originalEnd])) {
        originalEnd += 1;
    }
    while (proposedStart > 0 && isReviewTokenCharacter(proposed[proposedStart - 1])) {
        proposedStart -= 1;
    }
    while (proposedEnd < proposed.length && isReviewTokenCharacter(proposed[proposedEnd])) {
        proposedEnd += 1;
    }

    if (originalStart > 0 && original[originalStart - 1] === "(" && originalEnd < original.length && original[originalEnd] === ")") {
        originalStart -= 1;
        originalEnd += 1;
    }
    if (proposedStart > 0 && proposed[proposedStart - 1] === "(" && proposedEnd < proposed.length && proposed[proposedEnd] === ")") {
        proposedStart -= 1;
        proposedEnd += 1;
    }

    if (
        originalStart === hunk.originalStart &&
        originalEnd === hunk.originalEnd &&
        proposedStart === hunk.proposedStart &&
        proposedEnd === hunk.proposedEnd
    ) {
        return hunk;
    }

    const originalText = original.slice(originalStart, originalEnd);
    const proposedText = proposed.slice(proposedStart, proposedEnd);
    return {
        type: hunkType(originalText, proposedText),
        originalStart,
        originalEnd,
        proposedStart,
        proposedEnd,
        originalText,
        proposedText,
        tokens: buildInlineDiffTokens(originalText, proposedText),
    };
}

// Returns compact changed ranges for one proposal, avoiding full parent-chunk highlight boxes.
export function buildProposalHunks(original: string, proposed: string): ProposalHunk[] {
    if (original === proposed) return [];

    const window = trimEqualPrefixSuffix(original, proposed);
    const windowOriginal = original.slice(window.originalStart, window.originalEnd);
    const windowProposed = proposed.slice(window.proposedStart, window.proposedEnd);

    if (!windowOriginal || !windowProposed) {
        return [{
            type: hunkType(windowOriginal, windowProposed),
            originalStart: window.originalStart,
            originalEnd: window.originalEnd,
            proposedStart: window.proposedStart,
            proposedEnd: window.proposedEnd,
            originalText: windowOriginal,
            proposedText: windowProposed,
            tokens: buildInlineDiffTokens(windowOriginal, windowProposed),
        }];
    }

    const tokens = buildPositionedDiffTokens(windowOriginal, windowProposed);
    const hunks: ProposalHunk[] = [];
    let current: PositionedDiffToken[] = [];

    const flush = () => {
        if (!current.length) return;
        const originalStart = Math.min(...current.map((token) => token.originalStart));
        const originalEnd = Math.max(...current.map((token) => token.originalEnd));
        const proposedStart = Math.min(...current.map((token) => token.proposedStart));
        const proposedEnd = Math.max(...current.map((token) => token.proposedEnd));
        const originalText = windowOriginal.slice(originalStart, originalEnd);
        const proposedText = windowProposed.slice(proposedStart, proposedEnd);
        const hunk = {
            type: hunkType(originalText, proposedText),
            originalStart: window.originalStart + originalStart,
            originalEnd: window.originalStart + originalEnd,
            proposedStart: window.proposedStart + proposedStart,
            proposedEnd: window.proposedStart + proposedEnd,
            originalText,
            proposedText,
            tokens: mergeAdjacent(current.map(({ type, text }) => ({ type, text }))),
        };
        hunks.push(expandReplaceHunkToToken(original, proposed, hunk));
        current = [];
    };

    for (const token of tokens) {
        if (token.type === "equal") {
            flush();
            continue;
        }
        current.push(token);
    }
    flush();

    return hunks.length > 0 ? hunks : [{
        type: hunkType(windowOriginal, windowProposed),
        originalStart: window.originalStart,
        originalEnd: window.originalEnd,
        proposedStart: window.proposedStart,
        proposedEnd: window.proposedEnd,
        originalText: windowOriginal,
        proposedText: windowProposed,
        tokens: buildInlineDiffTokens(windowOriginal, windowProposed),
    }];
}
