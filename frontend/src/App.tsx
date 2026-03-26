import { Routes, Route, Navigate } from "react-router-dom";
import MainPage from "./pages/mainpage/MainPage";
import Login from "./pages/login/Login";
import RequireAuth from "./auth/RequireAuth";

function App() {
    return (
        <Routes>
            <Route
                path="/mainpage"
                element={(
                    <RequireAuth>
                        <MainPage />
                    </RequireAuth>
                )}
            />
            <Route path="/login" element={<Login />} />
            <Route path="*" element={<Navigate to="/mainpage" replace />} />
        </Routes>
    );
}

export default App;
