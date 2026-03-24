export const SUPPORTED_UPLOAD_EXTENSIONS = [
    ".pdf",
    ".doc",
    ".docx",
    ".txt",
    ".pptx",
    ".xlsx",
] as const;

export const FILE_INPUT_ACCEPT = SUPPORTED_UPLOAD_EXTENSIONS.join(",");

const EXTENSION_TO_MIME: Record<string, string> = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

export function getFileExtension(fileName: string): string {
    const lower = fileName.toLowerCase();
    const dotIndex = lower.lastIndexOf(".");
    if (dotIndex < 0) {
        return "";
    }
    return lower.slice(dotIndex);
}

export function isSupportedUploadFile(fileName: string): boolean {
    return getFileExtension(fileName) in EXTENSION_TO_MIME;
}

export function resolveUploadContentType(file: File): string {
    if (file.type) {
        return file.type;
    }
    const extension = getFileExtension(file.name);
    return EXTENSION_TO_MIME[extension] ?? "application/octet-stream";
}

