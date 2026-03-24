/// <reference types="vite/client" />

interface ImportMetaEnv {
    readonly VITE_API_BASE: string;
    readonly VITE_CHAT_TEST_USER_EMAIL?: string;
}

interface ImportMeta {
    readonly env: ImportMetaEnv;
}
