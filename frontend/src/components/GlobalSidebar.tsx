import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import "./GlobalSidebar.css";

export type GlobalSidebarMode = "collection" | "conversation";

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
    conversation: "global_sidebar_conversation_expanded",
};

const DEFAULT_EXPANDED: Record<GlobalSidebarMode, boolean> = {
    collection: false,
    conversation: false,
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
        to: "/conversation",
        label: "Conversation",
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
                <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z" />
                <path d="M8 9h8" />
                <path d="M8 13h5" />
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
    const isConversation = mode === "conversation";

    useEffect(() => {
        setIsExpanded(readInitialExpanded(mode));
    }, [mode]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(STORAGE_KEYS[mode], String(isExpanded));
    }, [mode, isExpanded]);

    const showNavItems = mode === "collection" || isExpanded || isConversation;
    const isButtonOnly = isConversation;
    const useSemisphereToggle = false;

    return (
        <aside
            className={[
                "global-sidebar",
                mode,
                isConversation ? "mainpage" : "",
                isExpanded ? "expanded" : "collapsed",
                isButtonOnly ? "button-only" : "",
                className,
            ].join(" ").trim()}
        >
            {isConversation && (
                <div className="global-sidebar-mainpage-brand">
                    <span className="global-sidebar-mainpage-dot" aria-hidden="true">KB</span>
                    <span>Knowledge Base</span>
                </div>
            )}
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
                                `global-sidebar-link ${isActive ? "active" : ""} ${isExpanded || isConversation ? "with-label" : "icon-only"}`
                            }
                            end={item.to === "/collections"}
                        >
                            <span className="global-sidebar-icon">{item.icon}</span>
                            <span className="global-sidebar-label">{item.label}</span>
                        </NavLink>
                    ))}
                </nav>
            )}
        </aside>
    );
}
