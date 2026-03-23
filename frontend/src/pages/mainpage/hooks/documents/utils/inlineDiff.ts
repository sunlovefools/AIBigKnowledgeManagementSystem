import type { InlineDiffToken } from "../../../types";

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

// Computes a whitespace-preserving token diff using an LCS walk.
export function buildInlineDiffTokens(original: string, proposed: string): InlineDiffToken[] {
    if (original === proposed) {
        return [{ type: "equal", text: original }];
    }

    const before = tokenizePreservingWhitespace(original);
    const after = tokenizePreservingWhitespace(proposed);
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

    const rawTokens: InlineDiffToken[] = [];
    let i = 0;
    let j = 0;
    while (i < beforeLength && j < afterLength) {
        if (before[i] === after[j]) {
            rawTokens.push({ type: "equal", text: before[i] });
            i += 1;
            j += 1;
            continue;
        }

        if (dp[i + 1][j] >= dp[i][j + 1]) {
            rawTokens.push({ type: "del", text: before[i] });
            i += 1;
        } else {
            rawTokens.push({ type: "add", text: after[j] });
            j += 1;
        }
    }

    while (i < beforeLength) {
        rawTokens.push({ type: "del", text: before[i] });
        i += 1;
    }
    while (j < afterLength) {
        rawTokens.push({ type: "add", text: after[j] });
        j += 1;
    }

    const normalized = rawTokens.map((token) =>
        token.type !== "equal" && /^\s+$/.test(token.text)
            ? { ...token, type: "equal" as const }
            : token
    );
    return mergeAdjacent(normalized);
}
