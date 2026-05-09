import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
    base:
        mode === "production"
            ? (process.env.VITE_BASE_PATH ?? "/AIBigKnowledgeManagementSystem/")
            : "/",
    plugins: [react()],
    server: {
        proxy: {
            "/api": "http://localhost:5000", // your local backend
        },
    },
    build: {
        outDir: "dist",
    },
}));
