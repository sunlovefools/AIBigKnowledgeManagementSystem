import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import type { DocumentItem } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

// Custom hook to manage documents in the modification panel
export function useDocuments(isModificationPanelOpen: boolean) {
    const [documents, setDocuments] = useState<DocumentItem[]>([]); // Array of documents fetched from the backend
    const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
    const [checkedDocs, setCheckedDocs] = useState<Set<string>>(new Set()); // Set of document IDs that is selected by user
    const [isLoadingDocs, setIsLoadingDocs] = useState(false);
    const [isDocsCached, setIsDocsCached] = useState(false);

    // Fetch documents from the backend API
    const fetchDocuments = useCallback(async () => { //Callback to memoize the function and prevent unnecessary re-renders
        setIsLoadingDocs(true);
        try {
            const response = await axios.get(`${API_BASE}/api/modifications/list`);
            const docs = response.data.documents
            setDocuments(docs);
            setIsDocsCached(true);

            if (docs.length > 0 && !selectedDocId) {
                setSelectedDocId(docs[0].id); // Auto-select the first document if none is selected
            }
        } catch (error) {
            console.error("Error fetching documents:", error);
        } finally {
            setIsLoadingDocs(false);
        }
    }, [selectedDocId]); // If user select a document, React will rerun this function to fetch documents again.

    // Effect to call fetchDocuments, when the modification panel is first opened, or when the cache is invalidated
    useEffect(() => {
        if (isModificationPanelOpen && !isDocsCached) {
            void fetchDocuments();
        }
    }, [isDocsCached, isModificationPanelOpen, fetchDocuments]); // If either 3 of the dependencies changes, React will rerun this effect to check if it needs to fetch documents again.

    // Handler to refresh the document list, mounted to the refresh button in the modification panel.
    const handleRefreshDocuments = useCallback(async () => {
        await fetchDocuments();
    }, [fetchDocuments]);

    // Handle selecting a document from the list, mounted to each document item in the modification panel.
    const handleDocumentSelect = useCallback((docId: string) => {
        setSelectedDocId(docId);
    }, []);

    // Handle checking/unchecking a document, mounted to the checkbox of each document item in the modification panel.
    const handleDocumentCheck = useCallback((docId: string, checked: boolean) => {
        setCheckedDocs((prev) => {
            const next = new Set(prev);
            if (checked) {
                next.add(docId);
            } else {
                next.delete(docId);
            }
            return next;
        });
    }, []);

    // A memorized value that computes the currently selected document based on the selectedDocId and the documents list.
    const selectedDocument = useMemo( // useMemo  to avoid unnecessary computations of selectedDocument on every render, it will only recompute when documents or selectedDocId changes.
        () => documents.find((doc) => doc.id === selectedDocId) ?? null,
        [documents, selectedDocId]
    );

    return {
        documents,
        selectedDocId,
        selectedDocument,
        checkedDocs,
        isLoadingDocs,
        fetchDocuments,
        handleRefreshDocuments,
        handleDocumentSelect,
        handleDocumentCheck,
    };
}
