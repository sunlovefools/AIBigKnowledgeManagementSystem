import { Routes, Route, Navigate } from "react-router-dom";
import MainPage from "./pages/mainpage/MainPage";
import CollectionPage from "./pages/collection/CollectionPage";
import ProfilePage from "./pages/profile/ProfilePage";
import Login from "./pages/login/Login";
import RequireAuth from "./auth/RequireAuth";

function App() {
    return (
        <Routes>
            <Route
                path="/collections"
                element={(
                    <RequireAuth>
                        <CollectionPage />
                    </RequireAuth>
                )}
            />
            <Route
                path="/mainpage"
                element={(
                    <RequireAuth>
                        <MainPage />
                    </RequireAuth>
                )}
            />
            <Route
                path="/profile"
                element={(
                    <RequireAuth>
                        <ProfilePage />
                    </RequireAuth>
                )}
            />
            <Route path="/login" element={<Login />} />
            <Route path="*" element={<Navigate to="/collections" replace />} />
        </Routes>
    );
}

export default App;
