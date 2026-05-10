import { useAuth0 } from "@auth0/auth0-react";
import axios from "axios";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { saveAuthSession } from "../../auth/session";
import {
    API_BASE,
    AUTH0_AUDIENCE,
    AUTH0_CLIENT_ID,
    AUTH0_DOMAIN,
    AUTH0_REDIRECT_URI,
    hasConfiguredValue,
} from "../../config/env";
import "./Login.css";

const auth0RedirectUri =
    AUTH0_REDIRECT_URI || new URL("login", `${window.location.origin}${import.meta.env.BASE_URL}`).toString();

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
                redirect_uri: auth0RedirectUri,
            },
        });
    };

    const hasAuth0Config =
        hasConfiguredValue(AUTH0_DOMAIN) &&
        hasConfiguredValue(AUTH0_CLIENT_ID) &&
        hasConfiguredValue(AUTH0_AUDIENCE);

    return (
        <div className="login-container">
            <div className="login-shell" aria-label="Documind sign in">
                <section className="login-brand-panel" aria-label="Documind overview">
                    <div className="login-brand-mark" aria-hidden="true">D</div>
                    <div>
                        <p className="login-kicker">Documind</p>
                        <h1>Private document intelligence for your workspace.</h1>
                        <p className="login-description">
                            Search, understand, and update your collections from one secure assistant.
                        </p>
                    </div>
                </section>

                <section className="login-card" aria-label="Sign in form">
                    <div className="login-card-header">
                        <div className="login-card-mark" aria-hidden="true">D</div>
                        <div>
                            <p className="login-card-eyebrow">Welcome back</p>
                            <h2>Sign in to Documind</h2>
                        </div>
                    </div>

                    <p className="login-auth0-note">
                        Continue with your organization account to access Documind.
                    </p>

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

                    <p className="login-security-copy">
                        By continuing, you will be redirected to Auth0 for secure authentication.
                    </p>
                </section>
            </div>
        </div>
    );
}
