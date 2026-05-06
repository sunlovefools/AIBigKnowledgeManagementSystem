import axios from "axios";
import { clearSessionAndRedirectToLogin, getAccessToken } from "./session";

export const apiClient = axios.create();

apiClient.interceptors.request.use((config) => {
    const token = getAccessToken();
    if (token) {
        config.headers = config.headers ?? {};
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

apiClient.interceptors.response.use(
    (response) => response,
    (error) => {
        if (error?.response?.status === 401) {
            clearSessionAndRedirectToLogin();
        }
        return Promise.reject(error);
    }
);

export async function authenticatedFetch(
    input: RequestInfo | URL,
    init?: RequestInit
): Promise<Response> {
    const headers = new Headers(init?.headers ?? {});
    const token = getAccessToken();
    if (token && !headers.has("Authorization")) {
        headers.set("Authorization", `Bearer ${token}`);
    }

    const response = await fetch(input, { ...init, headers });
    if (response.status === 401) {
        clearSessionAndRedirectToLogin();
    }
    return response;
}
