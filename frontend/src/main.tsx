import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Auth0Provider } from "@auth0/auth0-react";
import "./index.css";
import App from "./App.tsx";
import { BrowserRouter } from "react-router-dom";
import { UploadQueueProvider } from "./upload/UploadQueueContext";

const auth0Domain = import.meta.env.VITE_AUTH0_DOMAIN || "";
const auth0ClientId = import.meta.env.VITE_AUTH0_CLIENT_ID || "";
const auth0Audience = import.meta.env.VITE_AUTH0_AUDIENCE || "";

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <Auth0Provider
            domain={auth0Domain}
            clientId={auth0ClientId}
            authorizationParams={{
                redirect_uri: `${window.location.origin}/login`,
                audience: auth0Audience,
                scope: "openid profile email",
            }}
            cacheLocation="localstorage"
            useRefreshTokens
        >
            <BrowserRouter>
                <UploadQueueProvider>
                    <App />
                </UploadQueueProvider>
            </BrowserRouter>
        </Auth0Provider>
    </StrictMode>
);
