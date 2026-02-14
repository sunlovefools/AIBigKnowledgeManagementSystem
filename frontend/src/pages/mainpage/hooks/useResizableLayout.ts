import { useEffect, useState } from "react";

const SIDEBAR_WIDTH_KEY = "mainpage_sidebar_width";
const MODIFICATION_PANEL_WIDTH_KEY = "mainpage_mod_panel_width";
const SIDEBAR_OPEN_KEY = "mainpage_sidebar_open";

const DEFAULT_SIDEBAR_WIDTH = 300;
const DEFAULT_MODIFICATION_PANEL_WIDTH = 400;

const SIDEBAR_MIN_WIDTH = 240;
const SIDEBAR_MAX_WIDTH = 420;
const MODIFICATION_PANEL_MIN_WIDTH = 280;
const MODIFICATION_PANEL_MAX_WIDTH = 520;

const MOBILE_BREAKPOINT = 1024;

type ResizeTarget = "sidebar" | "mod-panel";

// A 
type DragState = {
    target: ResizeTarget;
    startX: number;
    startWidth: number;
};

// Utility function to clamp a number between a minimum and maximum value
const clamp = (value: number, min: number, max: number) =>
    Math.min(Math.max(value, min), max);

// Utility function to read a number from localStorage with a fallback value, ensures the returned value is a finite number
const readStoredNumber = (key: string, fallback: number) => {
    const rawValue = localStorage.getItem(key);
    if (!rawValue) return fallback;
    const parsed = Number(rawValue);
    return Number.isFinite(parsed) ? parsed : fallback;
};

// Custom hook to manage the the resizable layout of sidebar and modification panel
export function useResizableLayout() {
    const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);
    const [modPanelWidth, setModPanelWidth] = useState(DEFAULT_MODIFICATION_PANEL_WIDTH);
    const [isSidebarOpen, setIsSidebarOpen] = useState(true);
    const [isMobile, setIsMobile] = useState(false);
    const [dragState, setDragState] = useState<DragState | null>(null); // State to track the current drag operation for resizing

    // Effect to handle responsive layout changes based on viewport width, sets the isMobile state accordingly
    useEffect(() => {
        const mediaQuery = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`); // A media query to store the max width of the layout

        // Handler for media query changes, updates the isMobile state accordingly
        const handleMediaChange = (event: MediaQueryListEvent) => {
            setIsMobile(event.matches);
        };

        // Set the initial isMobile state based on the current viewport width and add the event listener for media query changes
        setIsMobile(mediaQuery.matches);
        mediaQuery.addEventListener("change", handleMediaChange);

        return () => {
            mediaQuery.removeEventListener("change", handleMediaChange);
        };
    }, []);

    // Effect to initialise the sidebar width and modification panel width 
    useEffect(() => {
        setSidebarWidth(
            // Ensure that the value is between the defined minimum and maximum
            clamp(
                readStoredNumber(SIDEBAR_WIDTH_KEY, DEFAULT_SIDEBAR_WIDTH),
                SIDEBAR_MIN_WIDTH,
                SIDEBAR_MAX_WIDTH,
            ),
        );
        setModPanelWidth(
            clamp(
                readStoredNumber(MODIFICATION_PANEL_WIDTH_KEY, DEFAULT_MODIFICATION_PANEL_WIDTH),
                MODIFICATION_PANEL_MIN_WIDTH,
                MODIFICATION_PANEL_MAX_WIDTH,
            ),
        );

        const storedSidebarOpen = localStorage.getItem(SIDEBAR_OPEN_KEY);
        if (storedSidebarOpen !== null) {
            setIsSidebarOpen(storedSidebarOpen === "true");
        }
    }, []);

    // Effects to store the sidebar width onto the localStorage whenever it changes, to prevent reset the width after page refresh
    useEffect(() => {
        localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth));
    }, [sidebarWidth]);

    // Effect to store the modification panel width onto the localStorage whenever it changes, to prevent reset the width after page refresh
    useEffect(() => {
        localStorage.setItem(MODIFICATION_PANEL_WIDTH_KEY, String(modPanelWidth));
    }, [modPanelWidth]);

    // Effect to store the sidebar open state onto the localStorage whenever it changes, to prevent reset the open state after page refresh
    useEffect(() => {
        localStorage.setItem(SIDEBAR_OPEN_KEY, String(isSidebarOpen));
    }, [isSidebarOpen]);

    // 
    useEffect(() => {
        if (!dragState || isMobile) {
            return;
        }

        const handleMouseMove = (event: MouseEvent) => {
            if (dragState.target === "sidebar") {
                const nextWidth = clamp(
                    dragState.startWidth + (event.clientX - dragState.startX),
                    SIDEBAR_MIN_WIDTH,
                    SIDEBAR_MAX_WIDTH,
                );
                setSidebarWidth(nextWidth);
                return;
            }

            const nextWidth = clamp(
                dragState.startWidth + (dragState.startX - event.clientX),
                MODIFICATION_PANEL_MIN_WIDTH,
                MODIFICATION_PANEL_MAX_WIDTH,
            );
            setModPanelWidth(nextWidth);
        };

        // Handler for mouseup event to end the resizing operation, resets the dragState to null
        const handleMouseUp = () => {
            setDragState(null);
        };

        document.body.style.userSelect = "none";
        document.body.style.cursor = "col-resize";
        window.addEventListener("mousemove", handleMouseMove);
        window.addEventListener("mouseup", handleMouseUp);

        return () => {
            document.body.style.userSelect = "";
            document.body.style.cursor = "";
            window.removeEventListener("mousemove", handleMouseMove);
            window.removeEventListener("mouseup", handleMouseUp);
        };
    }, [dragState, isMobile]);

    // Handler to set the dragState to start resizing the sidebar which is mounted to the resize handle of the sidebar
    const startSidebarResize = (startX: number) => {
        if (isMobile || !isSidebarOpen) return;
        setDragState({ target: "sidebar", startX, startWidth: sidebarWidth });
    };

    // Handler to set the dragState to start resizing the modification panel which is mounted to the resize handle of the modification panel
    const startModPanelResize = (startX: number) => {
        if (isMobile) return;
        setDragState({ target: "mod-panel", startX, startWidth: modPanelWidth });
    };

    // Handler to toggle the sidebar open state, which is mounted to the sidebar toggle button
    const toggleSidebar = () => {
        setIsSidebarOpen((prev) => !prev);
    };

    // Handler to close the sidebar, which can be used in various places such as the sidebar itself
    const closeSidebar = () => {
        setIsSidebarOpen(false);
    };

    return {
        sidebarWidth,
        modPanelWidth,
        isSidebarOpen,
        isMobile,
        isResizing: dragState !== null,
        toggleSidebar,
        closeSidebar,
        startSidebarResize,
        startModPanelResize,
    };
}
