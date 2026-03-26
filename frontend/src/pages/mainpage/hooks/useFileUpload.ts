import { useCallback, useState, type ChangeEventHandler } from "react";
import { apiClient } from "../../../auth/apiClient";
import {
    isSupportedUploadFile,
    resolveUploadContentType,
} from "../utils/uploadFormats";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

type UseFileUploadParams = {
    onUploadMessage: (message: string) => void;
    onUploadSuccess?: () => Promise<void> | void;
};

// Custom hook to manage file upload state and interactions
export function useFileUpload({ onUploadMessage, onUploadSuccess }: UseFileUploadParams) {
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [fileContent, setFileContent] = useState("");
    const [isUploading, setIsUploading] = useState(false);

    // Handler for file selection, reads the file content as a base64 string and updates the state accordingly
    const handleFileSelect: ChangeEventHandler<HTMLInputElement> = useCallback((event) => {
        const file = event.target.files?.[0] || null;

        if (file && !isSupportedUploadFile(file.name)) {
            setSelectedFile(null);
            setFileContent("");
            onUploadMessage(
                `Unsupported file type for "${file.name}". Please upload PDF, DOC, DOCX, TXT, PPTX, or XLSX.`
            );
            event.target.value = "";
            return;
        }

        setSelectedFile(file);

        if (file) {
            const reader = new FileReader();
            reader.onload = () => {
                const base64String = (reader.result as string).split(",")[1]; // Extract the base64-encoded string from the Data URL and set it to state
                setFileContent(base64String);
            };
            reader.readAsDataURL(file);
        } else {
            setFileContent("");
        }
    }, []);

    // Handler to clear the selected file and its content
    const clearFile = useCallback(() => {
        setSelectedFile(null);
        setFileContent("");
    }, []);

    // Handler to upload the selected file to the backend, sends a POST request with the file data and updates the chat with the result
    const handleUpload = useCallback(async () => {
        if (!fileContent || !selectedFile || isUploading) {
            return;
        }

        setIsUploading(true);
        try {
            await apiClient.post(`${API_BASE}/ingest/upload`, {
                fileName: selectedFile.name,
                contentType: resolveUploadContentType(selectedFile),
                data: fileContent,
            });

            onUploadMessage(`"${selectedFile.name}" has been added to the knowledge base.`);
            await onUploadSuccess?.();
            clearFile();
        } catch (error) {
            console.error("Error ingesting file:", error);
            onUploadMessage(`Failed to upload "${selectedFile?.name ?? "file"}".`);
        } finally {
            setIsUploading(false);
        }
    }, [
        clearFile,
        fileContent,
        isUploading,
        onUploadMessage,
        onUploadSuccess,
        selectedFile,
    ]);

    return {
        selectedFile,
        fileContent,
        isUploading,
        handleFileSelect,
        handleUpload,
        clearFile,
    };
}
