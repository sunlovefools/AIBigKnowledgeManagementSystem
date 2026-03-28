import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import "./GlobalSidebar.css";

export type GlobalSidebarMode = "collection" | "mainpage";

type GlobalSidebarProps = {
    mode: GlobalSidebarMode;
    className?: string;
};

type SidebarNavItem = {
    to: string;
    label: string;
    icon: ReactNode;
};

const STORAGE_KEYS: Record<GlobalSidebarMode, string> = {
    collection: "global_sidebar_collection_expanded",
    mainpage: "global_sidebar_mainpage_expanded",
};

const DEFAULT_EXPANDED: Record<GlobalSidebarMode, boolean> = {
    collection: false,
    mainpage: false,
};

const NAV_ITEMS: SidebarNavItem[] = [
    {
        to: "/collections",
        label: "Home",
        icon: (
            <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
            >
                <path d="M3 11.5 12 4l9 7.5" />
                <path d="M5.5 10.5V20h13V10.5" />
            </svg>
        ),
    },
    {
        to: "/profile",
        label: "Profile",
        icon: (
            <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
            >
                <circle cx="12" cy="8" r="3.25" />
                <path d="M5 19a7 7 0 0 1 14 0" />
            </svg>
        ),
    },
];

function readInitialExpanded(mode: GlobalSidebarMode): boolean {
    if (typeof window === "undefined") return DEFAULT_EXPANDED[mode];
    const stored = window.localStorage.getItem(STORAGE_KEYS[mode]);
    if (stored === "true") return true;
    if (stored === "false") return false;
    return DEFAULT_EXPANDED[mode];
}

export default function GlobalSidebar({ mode, className = "" }: GlobalSidebarProps) {
    const [isExpanded, setIsExpanded] = useState(() => readInitialExpanded(mode));

    useEffect(() => {
        setIsExpanded(readInitialExpanded(mode));
    }, [mode]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(STORAGE_KEYS[mode], String(isExpanded));
    }, [mode, isExpanded]);

    const showNavItems = mode === "collection" || isExpanded;
    const isButtonOnly = mode === "mainpage" && !isExpanded;
    const useSemisphereToggle = mode === "mainpage" && !isExpanded;

    return (
        <aside
            className={[
                "global-sidebar",
                mode,
                isExpanded ? "expanded" : "collapsed",
                isButtonOnly ? "button-only" : "",
                className,
            ].join(" ").trim()}
        >
            <button
                type="button"
                className={`global-sidebar-toggle ${useSemisphereToggle ? "semisphere" : ""}`}
                onClick={() => setIsExpanded((previous) => !previous)}
                aria-label={isExpanded ? "Collapse global sidebar" : "Expand global sidebar"}
                title={isExpanded ? "Collapse" : "Expand"}
            >
                <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                >
                    <polyline points={isExpanded ? "15 18 9 12 15 6" : "9 18 15 12 9 6"} />
                </svg>
            </button>

            {showNavItems && (
                <nav className="global-sidebar-nav" aria-label="Global navigation">
                    {NAV_ITEMS.map((item) => (
                        <NavLink
                            key={item.to}
                            to={item.to}
                            className={({ isActive }) =>
                                `global-sidebar-link ${isActive ? "active" : ""} ${isExpanded ? "with-label" : "icon-only"}`
                            }
                            end={item.to === "/collections"}
                        >
                            <span className="global-sidebar-icon">{item.icon}</span>
                            {isExpanded && <span className="global-sidebar-label">{item.label}</span>}
                        </NavLink>
                    ))}
                </nav>
            )}
        </aside>
    );
}
