import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { getAccessToken } from "./session";

type RequireAuthProps = {
    children: ReactNode;
};

export default function RequireAuth({ children }: RequireAuthProps) {
    if (!getAccessToken()) {
        return <Navigate to="/login" replace />;
    }
    return children;
}
