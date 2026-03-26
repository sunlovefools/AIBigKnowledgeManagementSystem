export type AuthProvider = "auth0" | "local";

type AuthResponse = {
    id: string;
    email: string;
    user_role: string;
    access_token: string;
    token_type?: string;
};

const TOKEN_STORAGE_KEY = "token";
const USER_EMAIL_STORAGE_KEY = "userEmail";
const USER_ROLE_STORAGE_KEY = "userRole";
const USER_ID_STORAGE_KEY = "userId";
const AUTH_PROVIDER_STORAGE_KEY = "authProvider";

export function getAccessToken(): string | null {
    const token = localStorage.getItem(TOKEN_STORAGE_KEY)?.trim();
    return token || null;
}

export function getAuthProvider(): AuthProvider | null {
    const provider = localStorage.getItem(AUTH_PROVIDER_STORAGE_KEY)?.trim();
    return provider === "auth0" || provider === "local" ? provider : null;
}

export function saveAuthSession(response: AuthResponse, provider: AuthProvider): void {
    localStorage.setItem(TOKEN_STORAGE_KEY, String(response.access_token || ""));
    localStorage.setItem(USER_EMAIL_STORAGE_KEY, String(response.email || ""));
    localStorage.setItem(USER_ROLE_STORAGE_KEY, String(response.user_role || "user"));
    localStorage.setItem(USER_ID_STORAGE_KEY, String(response.id || ""));
    localStorage.setItem(AUTH_PROVIDER_STORAGE_KEY, provider);
}

export function clearAuthSession(): void {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(USER_EMAIL_STORAGE_KEY);
    localStorage.removeItem(USER_ROLE_STORAGE_KEY);
    localStorage.removeItem(USER_ID_STORAGE_KEY);
    localStorage.removeItem(AUTH_PROVIDER_STORAGE_KEY);
}

export function redirectToLogin(): void {
    if (typeof window === "undefined") return;
    if (window.location.pathname !== "/login") {
        window.location.assign("/login");
    }
}

export function clearSessionAndRedirectToLogin(): void {
    clearAuthSession();
    redirectToLogin();
}
