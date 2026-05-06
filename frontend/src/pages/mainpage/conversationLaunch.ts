import type { ChatScope } from "./types";

export const CONVERSATION_LAUNCH_STORAGE_KEY = "kb.conversation.launch.v1";

export type ConversationLaunchPayload = {
    prompt?: string;
    scope: ChatScope;
};

export function saveConversationLaunch(payload: ConversationLaunchPayload): void {
    window.sessionStorage.setItem(CONVERSATION_LAUNCH_STORAGE_KEY, JSON.stringify(payload));
}

export function consumeConversationLaunch(): ConversationLaunchPayload | null {
    const raw = window.sessionStorage.getItem(CONVERSATION_LAUNCH_STORAGE_KEY);
    window.sessionStorage.removeItem(CONVERSATION_LAUNCH_STORAGE_KEY);
    if (!raw) return null;

    try {
        const parsed = JSON.parse(raw) as ConversationLaunchPayload;
        if (parsed?.scope?.type === "all_collections") return parsed;
        if (parsed?.scope?.type === "collection" && parsed.scope.collectionId) return parsed;
    } catch {
        return null;
    }
    return null;
}
