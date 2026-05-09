function _normalizeEnvValue(raw: string | undefined): string {
    if (!raw) return "";
    const trimmed = raw.trim();
    if (!trimmed) return "";
    if (trimmed.startsWith("#")) return "";
    return trimmed.replace(/\s+#.*$/, "").trim();
}

function _toApiBase(raw: string | undefined): string {
    const normalized = _normalizeEnvValue(raw);
    const fallback = "http://localhost:8000";
    return (normalized || fallback).replace(/\/$/, "");
}

export const API_BASE = _toApiBase(import.meta.env.VITE_API_BASE);
export const AUTH0_DOMAIN = _normalizeEnvValue(import.meta.env.VITE_AUTH0_DOMAIN);
export const AUTH0_CLIENT_ID = _normalizeEnvValue(import.meta.env.VITE_AUTH0_CLIENT_ID);
export const AUTH0_AUDIENCE = _normalizeEnvValue(import.meta.env.VITE_AUTH0_AUDIENCE);
export const AUTH0_REDIRECT_URI = _normalizeEnvValue(import.meta.env.VITE_AUTH0_REDIRECT_URI);
export const AUTH0_LOGOUT_RETURN_TO = _normalizeEnvValue(import.meta.env.VITE_AUTH0_LOGOUT_RETURN_TO);
export const CHAT_TEST_USER_EMAIL = _normalizeEnvValue(import.meta.env.VITE_CHAT_TEST_USER_EMAIL);

export function canRunAuth0InCurrentOrigin(): boolean {
    if (typeof window === "undefined") return true;
    const { protocol, hostname } = window.location;
    return protocol === "https:" || hostname === "localhost" || hostname === "127.0.0.1";
}

export function hasConfiguredValue(value: string): boolean {
    const normalized = value.trim();
    return normalized.length > 0 && !normalized.startsWith("your-");
}
