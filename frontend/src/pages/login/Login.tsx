import { useAuth0 } from "@auth0/auth0-react";
import axios from "axios";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { saveAuthSession } from "../../auth/session";
import "./Login.css";

const API_BASE = (import.meta.env.VITE_API_BASE || "http://localhost:8000").replace(/\/$/, "");
const AUTH0_AUDIENCE = import.meta.env.VITE_AUTH0_AUDIENCE || "";
const AUTH0_DOMAIN = import.meta.env.VITE_AUTH0_DOMAIN || "";
const AUTH0_CLIENT_ID = import.meta.env.VITE_AUTH0_CLIENT_ID || "";

function hasConfiguredValue(value: string): boolean {
    const normalized = value.trim();
    return normalized.length > 0 && !normalized.startsWith("your-");
}

export default function Login() {
    const navigate = useNavigate();
    const [exchangeError, setExchangeError] = useState("");
    const [isExchanging, setIsExchanging] = useState(false);
    const exchangeAttemptedRef = useRef(false);
    const {
        loginWithRedirect,
        getAccessTokenSilently,
        isAuthenticated,
        isLoading,
        error,
    } = useAuth0();

    useEffect(() => {
        const token = localStorage.getItem("token")?.trim();
        if (token) {
            navigate("/collections", { replace: true });
        }
    }, [navigate]);

    useEffect(() => {
        if (!isAuthenticated || exchangeAttemptedRef.current) {
            return;
        }

        exchangeAttemptedRef.current = true;
        let isMounted = true;

        const exchangeAuth0Token = async () => {
            setExchangeError("");
            setIsExchanging(true);
            try {
                const auth0Token = await getAccessTokenSilently({
                    authorizationParams: {
                        audience: AUTH0_AUDIENCE,
                        scope: "openid profile email",
                    },
                });

                const response = await axios.post(`${API_BASE}/auth/auth0-login`, {
                    token: auth0Token,
                });

                saveAuthSession(response.data, "auth0");
                navigate("/collections", { replace: true });
            } catch (exchangeErr) {
                let errorMessage = "OAuth login failed. Please try again.";

                if (axios.isAxiosError(exchangeErr)) {
                    const backendDetail = exchangeErr.response?.data?.detail;
                    if (typeof backendDetail === "string" && backendDetail.trim().length > 0) {
                        errorMessage = backendDetail;
                    } else {
                        errorMessage = `OAuth login failed (HTTP ${exchangeErr.response?.status ?? "unknown"}).`;
                    }
                }

                console.error("Auth0 token exchange failed:", {
                    message: exchangeErr instanceof Error ? exchangeErr.message : String(exchangeErr),
                    axiosStatus: axios.isAxiosError(exchangeErr) ? exchangeErr.response?.status : undefined,
                    axiosData: axios.isAxiosError(exchangeErr) ? exchangeErr.response?.data : undefined,
                });
                if (isMounted) {
                    setExchangeError(errorMessage);
                }
                exchangeAttemptedRef.current = false;
            } finally {
                if (isMounted) {
                    setIsExchanging(false);
                }
            }
        };

        void exchangeAuth0Token();

        return () => {
            isMounted = false;
        };
    }, [getAccessTokenSilently, isAuthenticated, navigate]);

    const handleOAuthLogin = async () => {
        setExchangeError("");
        await loginWithRedirect({
            authorizationParams: {
                audience: AUTH0_AUDIENCE,
                scope: "openid profile email",
                redirect_uri: `${window.location.origin}/login`,
            },
        });
    };

    const hasAuth0Config =
        hasConfiguredValue(AUTH0_DOMAIN) &&
        hasConfiguredValue(AUTH0_CLIENT_ID) &&
        hasConfiguredValue(AUTH0_AUDIENCE);

    return (
        <div className="login-container">
            <div className="login-card">
                <p className="login-kicker">Team44 Workspace</p>
                <h1>Sign in</h1>
                <p className="login-description">
                    Minimal, secure access to your document and chat workspace.
                </p>
                <p className="login-auth0-note">Only login with Auth0 is supported.</p>

                {!hasAuth0Config && (
                    <p className="login-message error">
                        Missing Auth0 config. Set `VITE_AUTH0_DOMAIN`, `VITE_AUTH0_CLIENT_ID`, and
                        `VITE_AUTH0_AUDIENCE`.
                    </p>
                )}

                {error && <p className="login-message error">Auth0 error: {error.message}</p>}
                {exchangeError && <p className="login-message error">{exchangeError}</p>}

                <button
                    type="button"
                    className="login-button"
                    onClick={() => void handleOAuthLogin()}
                    disabled={isLoading || isExchanging || !hasAuth0Config}
                >
                    {isLoading || isExchanging ? "Signing in..." : "Continue with Auth0"}
                </button>
            </div>
        </div>
    );
}
