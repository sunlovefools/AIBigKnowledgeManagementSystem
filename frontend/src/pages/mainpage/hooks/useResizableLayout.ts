import { useEffect, useRef, useState } from "react";

const SIDEBAR_WIDTH_KEY = "mainpage_sidebar_width";
const MODIFICATION_PANEL_WIDTH_KEY = "mainpage_mod_panel_width";
const SIDEBAR_OPEN_KEY = "mainpage_sidebar_open";

const DEFAULT_SIDEBAR_WIDTH = 300;
const DEFAULT_MODIFICATION_PANEL_WIDTH = 400;

const SIDEBAR_MIN_WIDTH = 240;
const SIDEBAR_MAX_WIDTH = 420;
const MODIFICATION_PANEL_MIN_WIDTH = 360;
const MODIFICATION_PANEL_MAX_WIDTH = 1000;
const DESKTOP_PRIMARY_STAGE_MIN_WIDTH = 320;
const RESIZE_HANDLE_WIDTH = 8;

const MOBILE_BREAKPOINT = 1024;
const SIDEBAR_TOGGLE_TRANSITION_MS = 220;

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

const getMaxModificationPanelWidth = (isSidebarOpen: boolean, sidebarWidth: number) => {
    const occupiedSidebarWidth = isSidebarOpen ? sidebarWidth + RESIZE_HANDLE_WIDTH : 0;
    const availableMainWidth = window.innerWidth - occupiedSidebarWidth;
    const maxWidth = availableMainWidth - RESIZE_HANDLE_WIDTH - DESKTOP_PRIMARY_STAGE_MIN_WIDTH;
    return Math.max(MODIFICATION_PANEL_MIN_WIDTH, Math.min(maxWidth, MODIFICATION_PANEL_MAX_WIDTH));
};

// Utility function to read a number from localStorage with a fallback value, ensures the returned value is a finite number
const readStoredNumber = (key: string, fallback: number) => {
    const rawValue = localStorage.getItem(key);
    if (!rawValue) return fallback;
    const parsed = Number(rawValue);
    return Number.isFinite(parsed) ? parsed : fallback;
};

type ResizableLayoutOptions = {
    defaultSidebarOpen?: boolean;
    restoreSidebarOpen?: boolean;
};

// Custom hook to manage the the resizable layout of sidebar and modification panel
export function useResizableLayout(options: ResizableLayoutOptions = {}) {
    const defaultSidebarOpen = options.defaultSidebarOpen ?? true;
    const restoreSidebarOpen = options.restoreSidebarOpen ?? true;
    const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);
    const [modPanelWidth, setModPanelWidth] = useState(DEFAULT_MODIFICATION_PANEL_WIDTH);
    const [isSidebarOpen, setIsSidebarOpen] = useState(defaultSidebarOpen);
    const [isMobile, setIsMobile] = useState(false);
    const [dragState, setDragState] = useState<DragState | null>(null); // State to track the current drag operation for resizing
    const [isSidebarToggling, setIsSidebarToggling] = useState(false);
    const sidebarToggleTimeoutRef = useRef<number | null>(null);

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
        const initialSidebarWidth = clamp(
            readStoredNumber(SIDEBAR_WIDTH_KEY, DEFAULT_SIDEBAR_WIDTH),
            SIDEBAR_MIN_WIDTH,
            SIDEBAR_MAX_WIDTH,
        );
        const storedSidebarOpen = localStorage.getItem(SIDEBAR_OPEN_KEY);
        const initialSidebarOpen = restoreSidebarOpen && storedSidebarOpen !== null
            ? storedSidebarOpen === "true"
            : defaultSidebarOpen;

        setSidebarWidth(initialSidebarWidth);
        setModPanelWidth(
            clamp(
                readStoredNumber(MODIFICATION_PANEL_WIDTH_KEY, DEFAULT_MODIFICATION_PANEL_WIDTH),
                MODIFICATION_PANEL_MIN_WIDTH,
                getMaxModificationPanelWidth(initialSidebarOpen, initialSidebarWidth),
            ),
        );
        if (restoreSidebarOpen && storedSidebarOpen !== null) {
            setIsSidebarOpen(initialSidebarOpen);
        }
    }, [defaultSidebarOpen, restoreSidebarOpen]);

    // Effects to store the sidebar width onto the localStorage whenever it changes, to prevent reset the width after page refresh
    useEffect(() => {
        localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth));
    }, [sidebarWidth]);

    // Effect to store the modification panel width onto the localStorage whenever it changes, to prevent reset the width after page refresh
    useEffect(() => {
        localStorage.setItem(MODIFICATION_PANEL_WIDTH_KEY, String(modPanelWidth));
    }, [modPanelWidth]);

    useEffect(() => {
        if (isMobile) return;

        const syncModPanelWidth = () => {
            setModPanelWidth((currentWidth) =>
                clamp(
                    currentWidth,
                    MODIFICATION_PANEL_MIN_WIDTH,
                    getMaxModificationPanelWidth(isSidebarOpen, sidebarWidth),
                ),
            );
        };

        syncModPanelWidth();
        window.addEventListener("resize", syncModPanelWidth);

        return () => {
            window.removeEventListener("resize", syncModPanelWidth);
        };
    }, [isMobile, isSidebarOpen, sidebarWidth]);

    // Effect to store the sidebar open state onto the localStorage whenever it changes, to prevent reset the open state after page refresh
    useEffect(() => {
        localStorage.setItem(SIDEBAR_OPEN_KEY, String(isSidebarOpen));
    }, [isSidebarOpen]);

    // Effect to handle the mouse move and mouse up events during resizing, updates the corresponding width state based on the current dragState and cleans up the event listeners after resizing is done
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
                getMaxModificationPanelWidth(isSidebarOpen, sidebarWidth),
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
    }, [dragState, isMobile, isSidebarOpen, sidebarWidth]);

    useEffect(() => {
        return () => {
            if (sidebarToggleTimeoutRef.current !== null) {
                window.clearTimeout(sidebarToggleTimeoutRef.current);
            }
        };
    }, []);

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
        if (sidebarToggleTimeoutRef.current !== null) {
            window.clearTimeout(sidebarToggleTimeoutRef.current);
        }

        setIsSidebarToggling(true);
        setIsSidebarOpen((prev) => !prev);

        sidebarToggleTimeoutRef.current = window.setTimeout(() => {
            setIsSidebarToggling(false);
            sidebarToggleTimeoutRef.current = null;
        }, SIDEBAR_TOGGLE_TRANSITION_MS + 40);
    };

    // Handler to close the sidebar, which can be used in various places such as the sidebar itself
    const closeSidebar = () => {
        setIsSidebarOpen(false);
    };

    const openSidebar = () => {
        setIsSidebarOpen(true);
    };

    return {
        sidebarWidth,
        modPanelWidth,
        isSidebarOpen,
        isMobile,
        isResizing: dragState !== null,
        isSidebarToggling,
        toggleSidebar,
        closeSidebar,
        openSidebar,
        startSidebarResize,
        startModPanelResize,
    };
}
