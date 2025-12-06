# Frontend Changes Log - Drag and Drop File Upload

**Date:** 2025-12-06
**Feature:** Drag and Drop File Upload & Code Refactoring

## Summary
Implemented a drag-and-drop file upload feature for the main chat interface and refactored the existing file upload logic to be more modular and reusable.

## Detailed Changes

### 1. Refactoring (`frontend/src/pages/mainpage/MainPage.tsx`)
- **Extracted Helper Functions:**
  - `readFileAsBase64(file: File): Promise<string>`: Encapsulates the logic for reading a file and converting it to a Base64 string.
  - `uploadFileToBackend(file: File, base64Content: string): Promise<boolean>`: Encapsulates the API call to `/ingest/webhook` for file ingestion.
- **Updated Existing Functions:**
  - `handleSend`: Now uses `uploadFileToBackend` for file uploads, separating the logic from the text query execution.
  - `onFileChange`: Now uses `readFileAsBase64` to process selected files. Fixed an issue where `await` was used without `async`.

### 2. New Feature: Drag and Drop (`frontend/src/pages/mainpage/MainPage.tsx`)
- **State Management:**
  - Added `isDragging` state to track when a user is dragging a file over the interface.
- **Event Handlers:**
  - `handleDragOver`: Sets `isDragging` to `true` to show visual feedback.
  - `handleDragLeave`: Sets `isDragging` to `false` to remove visual feedback.
  - `handleDrop`: 
    - Prevents default browser behavior.
    - Retrieves the dropped file.
    - Immediately converts the file to Base64 and uploads it using the new helper functions.
    - Updates the chat history with "Uploaded file: [name]" and the subsequent success/failure message from the system.
- **UI Integration:**
  - Attached drag event listeners to the main `.app-root` container.
  - Dynamically adds the `dragging` CSS class to the container based on state.

### 3. Styling (`frontend/src/pages/mainpage/MainPage.css`)
- **Visual Feedback:**
  - Added styles for `.app-root.dragging`.
  - Used a `::after` pseudo-element to create a full-screen overlay when dragging.
  - Overlay includes a dashed blue border, a semi-transparent blue background, and the text "Drop file to upload".

## Usage
- **Drag & Drop:** Users can drag a file anywhere onto the chat interface. The file will be automatically uploaded and ingested.
- **Manual Selection:** The existing paperclip icon functionality remains unchanged but now uses the shared underlying logic.
