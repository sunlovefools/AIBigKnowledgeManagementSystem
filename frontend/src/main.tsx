import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Auth0Provider } from "@auth0/auth0-react";
import "./index.css";
import App from "./App.tsx";
import { BrowserRouter } from "react-router-dom";
import { UploadQueueProvider } from "./upload/UploadQueueContext";
import {
    AUTH0_AUDIENCE,
    AUTH0_CLIENT_ID,
    AUTH0_DOMAIN,
    AUTH0_REDIRECT_URI,
} from "./config/env";

const auth0RedirectUri = AUTH0_REDIRECT_URI || `${window.location.origin}/login`;

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <Auth0Provider
            domain={AUTH0_DOMAIN}
            clientId={AUTH0_CLIENT_ID}
            authorizationParams={{
                redirect_uri: auth0RedirectUri,
                audience: AUTH0_AUDIENCE,
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
