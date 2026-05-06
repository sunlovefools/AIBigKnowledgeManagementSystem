import { useAuth0 } from "@auth0/auth0-react";
import { useNavigate } from "react-router-dom";
import GlobalSidebar from "../../components/GlobalSidebar";
import { clearAuthSession, getAuthProvider } from "../../auth/session";
import { AUTH0_LOGOUT_RETURN_TO } from "../../config/env";
import "./ProfilePage.css";

export default function ProfilePage() {
    const navigate = useNavigate();
    const { logout } = useAuth0();

    const userEmail = localStorage.getItem("userEmail")?.trim() || "Unknown";
    const provider = getAuthProvider() || "local";
    const logoutReturnTo = AUTH0_LOGOUT_RETURN_TO || window.location.origin;

    const handleLogout = () => {
        const authProvider = getAuthProvider();
        clearAuthSession();

        if (authProvider === "auth0") {
            logout({
                logoutParams: {
                    returnTo: logoutReturnTo,
                },
            });
            return;
        }

        navigate("/login", { replace: true });
    };

    return (
        <div className="profile-page-shell">
            <GlobalSidebar mode="collection" />

            <main className="profile-page-main">
                <section className="profile-card">
                    <div className="profile-eyebrow">Profile</div>
                    <h1>Account</h1>
                    <div className="profile-row">
                        <span className="profile-label">Email</span>
                        <span className="profile-value">{userEmail}</span>
                    </div>
                    <div className="profile-row">
                        <span className="profile-label">Provider</span>
                        <span className="profile-value">{provider}</span>
                    </div>
                    <div className="profile-actions">
                        <button type="button" className="profile-logout-btn" onClick={handleLogout}>
                            Logout
                        </button>
                    </div>
                </section>
            </main>
        </div>
    );
}
