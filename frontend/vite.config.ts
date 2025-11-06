import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  // Set base path for production build and development
  // If run npm run build, the base path will be /AIBigKnowledgeManagementSystem/
  // If run npm run dev, the base path will be /
  base: mode === 'production' ? '/AIBigKnowledgeManagementSystem/' : '/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:5000' // your local backend
    }
  },
  build: {
    outDir: 'dist'
  }
}))
