import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import LoginPage from './pages/LoginPage.tsx'
import App from './App.tsx'//temp file created for testing backend

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/*Uncomment if want to use App file: <App />*/}
    <LoginPage />
  </StrictMode>,
)
