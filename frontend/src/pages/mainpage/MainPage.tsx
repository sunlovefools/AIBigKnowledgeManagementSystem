import React, { useState, useRef, useEffect } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "./MainPage.css";
import "highlight.js/styles/github-dark.css";

/**
 * Defines the structure for messages in the chat state array.
 * 
 * NOTE: The 'fileName' is critical for rendering file attachment visuals in the chat history.
 */
type ChatMessage = { 
    role: "user" | "ai";
    text: string; 
    fileName?: string // Optional: name of the attached file
};

// Get the backend API base URL from environment variables
const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

/**
 * Renders the main chat application page, orchestrating user interaction, state management,
 * and communication with the backend for queries and file ingestion.
 *
 * @returns {JSX.Element} The full-page AI chat interface.
 */
export default function MainPage() {
    // function to change the page URL, can use it for logging out.
    const navigate = useNavigate();

    // --- State Variables ---

    // History of all messages (User queries and AI responses)
    const [messages, setMessages] = useState<ChatMessage[]>([]);

    // Text currently typed in the input box
    const [input, setInput] = useState("");

    // File object currently selected by the user
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    
    // The raw content of the selected file in base64 format
    const [fileContent, setFileContent] = useState<string>("");
    
    // State to hold the response message from the backend
    const [response, setResponse] = useState<string>("");

    // State to track if a file is being dragged over the drop zone
    const [isDragging, setIsDragging] = useState(false);

    // --- References for DOM Interaction ---
    // 'fileRef' gives us a way to "click" the hidden file input element.
    const fileRef = useRef<HTMLInputElement | null>(null);

    // 'listRef' gives us a way to control the message area, specifically to make it scroll.
    const listRef = useRef<HTMLDivElement | null>(null);

    /**
     * Helper: Reads a file and returns its Base64 content.
     */
    const readFileAsBase64 = (file: File): Promise<string> => {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const base64String = (reader.result as string).split(",")[1];
                resolve(base64String);
            };
            reader.onerror = (error) => reject(error);
            reader.readAsDataURL(file);
        });
    };

    /**
     * Helper: Uploads a file to the backend ingestion endpoint.
     */
    const uploadFileToBackend = async (file: File, base64Content: string) => {
        try {
            const response = await axios.post(`${API_BASE}/ingest/webhook`, {
                fileName: file.name,
                contentType: file.type || "application/octet-stream",
                data: base64Content,
            });
            console.log("File ingestion response:", response.data);
            return true;
        } catch (error) {
            console.error("Error uploading file:", error);
            return false;
        }
    };

    /**
     * Effect to auto-scroll the message area to the bottom whenever a new message is added.
     */
    useEffect(() => {
        // Get the message area element.
        const element = listRef.current;
        if (element) {
            // This makes it automatically scroll to the bottom to show the newest message.
            element.scrollTop = element.scrollHeight;
        }
    }, [messages]);

    /**
     * Clears the authentication token and redirects the user to the registration page.
     */
    const handleLogout = () => {
        localStorage.removeItem("token");
        navigate("/register");
    };

    /**
     * Sends the user's query and the attached file (if any) to the backend API.
     * This function performs two sequential or concurrent API calls: query and ingestion.
     */
    const handleSend =async () => {
        const textInput = input.trim();

        // WHY: Return if there are no text or file to send.
        if (!textInput && !selectedFile) return;

        // 1. Prepare and add the user's message to the chat history.
        const newMessage: ChatMessage = {
            role: "user",
            text: textInput,
            fileName: selectedFile ? selectedFile.name : undefined, // If a file is picked, add its name.
        };
        setMessages((messagesArray) => [...messagesArray, newMessage]);

        // 2. Send user's text query to backend /api/query endpoint
        if (textInput) {
            try {
                const response = await axios.post(`${API_BASE}/api/query`, {
                    query: textInput,
                });

                // 3. Update the chat history with the AI's response.
                setMessages((messagesArray) => [
                    ...messagesArray,
                    { role: "ai", text: response.data.answer || "(no response)" },
                ]);
            } catch (error) {
                console.error("Error sending query:", error);
                // Provide feedback to user in chat history if fail to connect to backend
                setMessages((messagesArray) => [
                    ...messagesArray,
                    { role: "ai", text: "❌ Error: Unable to reach backend" },
                ]);
            }
        }

        // 4. Send the selected file (if any) to backend /ingest/webhook endpoint
        if (selectedFile && fileContent){
            const success = await uploadFileToBackend(selectedFile, fileContent);
            if (!success) {
                 console.error("Failed to upload file via handleSend");
            }
        }

        // 5. Clear the input box and selected file after sending.
        setInput("");
        clearFile();
    };

    /**
     * Handles drag over event to show visual feedback.
     */
    const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setIsDragging(true);
    };

    /**
     * Handles drag leave event to remove visual feedback.
     */
    const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setIsDragging(false);
    };

    /**
     * Handles file drop event: automatically uploads the dropped file.
     */
    const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setIsDragging(false);

        const file = e.dataTransfer.files?.[0];
        if (file) {
            // 1. Show file in chat as "User uploaded a file"
            const newMessage: ChatMessage = {
                role: "user",
                text: `Uploaded file: ${file.name}`,
                fileName: file.name,
            };
            setMessages((prev) => [...prev, newMessage]);

            try {
                // 2. Convert to Base64
                const base64 = await readFileAsBase64(file);
                
                // 3. Upload immediately
                const success = await uploadFileToBackend(file, base64);
                
                // 4. Show system response
                if (success) {
                    setMessages((prev) => [...prev, { role: "ai", text: `✅ File "${file.name}" uploaded and ingested successfully.` }]);
                } else {
                    setMessages((prev) => [...prev, { role: "ai", text: `❌ Failed to upload "${file.name}".` }]);
                }
            } catch (error) {
                console.error("Error processing dropped file:", error);
                setMessages((prev) => [...prev, { role: "ai", text: `❌ Error processing file "${file.name}".` }]);
            }
        }
    };

    /**
     * Programmatically triggers the click event on the hidden file input element.
     */
    const handleFileSelectClick = () => {
        fileRef.current?.click();
    };

    /**
     * Handles the file selection event: saves the file and converts its content to a Base64 string.
     *
     * WHY: Base64 conversion is necessary here because the backend API expects file data in a JSON payload.
     * @param {React.ChangeEvent<HTMLInputElement>} event The file change event from the input.
     */
    const onFileChange: React.ChangeEventHandler<HTMLInputElement> = async (event) => {
        const file = event.target.files?.[0] || null;
        setSelectedFile(file);

        if (file) {
            try {
                const base64String = await readFileAsBase64(file);
                setFileContent(base64String);
            } catch (error) {
                console.error("Error reading file:", error);
                setFileContent("");
            }
        } else {
            setFileContent("");
        }
    };
    
   /**
     * Resets the selected file state and clears the file input element's value.
    */
    const clearFile = () => {
        setSelectedFile(null);
        if (fileRef.current) fileRef.current.value = "";
    };

  /**
    * Sends a simple GET request to a backend health check endpoint.
  */
  const handleBackendTestClick = async () => {
    try {
      const response = await axios.get(`${API_BASE}/hello`);
      // setResponse updates the state variable 'response' with the message from the backend
      setResponse(response.data.message);
    } catch (error) {
      console.error("Error connecting to backend:", error);
      setResponse("Error connecting to backend");
    }
  };

   return (
    <div 
        className={`app-root ${isDragging ? "dragging" : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
    >
      {/* Header */}
      <header className="app-header">
        <div className="app-title">Your AI Knowledge System</div>
        <div className="header-actions">
          <div className="user-badge">👤 User</div>
          <button className="logout-button" onClick={handleLogout}>
            Logout
          </button>
          {/* Hidden backend test button */}
          <button className="dev-test-button" onClick={handleBackendTestClick} title="Backend test">
            ⚙️
          </button>
        </div>
      </header>

      {/* Chat Section */}
      <div className="chat-container">
        <div className="messages-area" ref={listRef}>
          {!messages.length ? (
            <div className="welcome-screen">
              <div className="welcome-icon">✨</div>
              <h2 className="welcome-title">How can I help you today?</h2>
              <p className="welcome-subtitle">
                Upload documents, ask questions, and collaborate with your AI workspace
              </p>
            </div>
          ) : (
            <div className="messages-column">
              {messages.map((message, index) => (
                <div
                  key={index}
                  className={`message ${message.role === "user" ? "user" : "ai"}`}
                >
                  <div className="message-avatar">
                    {message.role === "user" ? "👤" : "🤖"}
                  </div>
                  <div className="message-content">
                    {message.text && (
                      <div className="message-text">
                        {message.role === "ai" ? (
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            rehypePlugins={[rehypeHighlight]}
                            components={{
                              code({ node, inline, className, children, ...props }: any) {
                                return inline ? (
                                  <code className="inline-code" {...props}>
                                    {children}
                                  </code>
                                ) : (
                                  <code className={className} {...props}>
                                    {children}
                                  </code>
                                );
                              },
                            }}
                          >
                            {message.text}
                          </ReactMarkdown>
                        ) : (
                          message.text
                        )}
                      </div>
                    )}
                    {message.fileName && (
                      <div className="message-file-attachment">
                        <span>{message.fileName}</span>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Input fixed bottom */}
        <div className="input-area">
          <div className="input-wrapper">
            <button
              className="upload-button"
              title="Upload file"
              onClick={handleFileSelectClick}
            >
              📎
            </button>
            <input
              ref={fileRef}
              type="file"
              className="file-input"
              onChange={onFileChange}
              accept="image/*,.pdf,.doc,.docx,.txt"
            />
            {selectedFile && (
              <div className="uploaded-file">
                📄 {selectedFile.name}
                <button
                  className="remove-file"
                  onClick={clearFile}
                  title="Remove file"
                >
                  ✕
                </button>
              </div>
            )}
            <textarea
              className="message-input"
              placeholder="Type your message..."
              rows={1}
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
            <button
              className="send-button"
              onClick={handleSend}
              disabled={!input.trim()}
              title="Send"
            >
              <svg
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path
                  d="M3.4 20.4L20.85 12.02L3.4 3.6V10.29L15.3 12.02L3.4 13.75V20.4Z"
                  fill="white"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* hidden backend test response */}
      {response && <div className="backend-response">{response}</div>}
    </div>
  );
}