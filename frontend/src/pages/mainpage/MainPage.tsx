import React, { useState, useRef, useEffect } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import "./MainPage.css";

// blueprint for a chat message, must have a role (user/AI), might have a filename.
type ChatMessage = { role: "user" | "ai"; text: string; fileName?: string };

// Get the backend API base URL from environment variables, during development it is usually http://
const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

export default function MainPage() {
    // function to change the page URL, can use it for logging out.
    const navigate = useNavigate();

    // chat state
    // 'messages' is an array that will hold all of our chat message objects.
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    // 'input' will hold the text that the user is currently typing in the textbox.
    const [input, setInput] = useState("");
    // 'selectedFile' will hold the file that the user has selected.
    // Better name for clarity
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    // 'fileRef' gives us a way to "click" the hidden file input element.
    const fileRef = useRef<HTMLInputElement | null>(null);
    // 'listRef' gives us a way to control the message area, specifically to make it scroll.
    const listRef = useRef<HTMLDivElement | null>(null);
    // State to hold the response message from the backend
    const [response, setResponse] = useState<string>("");
    // Store the 64encoded content of the file selected by the user
    const [fileContent, setFileContent] = useState<string>("");

    // runs every time the 'messages' array changes (i.e., when a new message is added).
    useEffect(() => {
        // Get the message area element.
        const el = listRef.current;
        if (el) {
            // This makes it automatically scroll to the bottom to show the newest message.
            el.scrollTop = el.scrollHeight;
        }
    }, [messages]);

    /* logging out: clears token and redirects to /register */
    const handleLogout = () => {
        localStorage.removeItem("token");
        navigate("/register");
    };

    /* query placeholder: add message + dummy AI reply */
    const handleSend =async () => {
        // Trim whitespace from the input text.
        const textInput = input.trim();
        // Do nothing if there's no text AND no file selected.
        if (!textInput && !selectedFile) return;
        // Create a new message object that includes text and an optional fileName (For displaying it later)
        const newMessage: ChatMessage = {
            role: "user",
            text: textInput,
            fileName: selectedFile ? selectedFile.name : undefined, // If a file is picked, add its name.
        };
        // Add our new message object to the end of the 'messages' array.
        setMessages((messagesArray) => [...messagesArray, newMessage]);

        // Here, you send the fileContent and its title to the backend
        if (fileContent){
          try {
              // Send POST request to backend /ingest/webhook endpoint along with fileContent only if a file is selected
              const res = await axios.post(`${API_BASE}/ingest/webhook`, {
                fileName: selectedFile ? selectedFile.name : "Untitled",
                contentType: selectedFile ? selectedFile.type : "application/octet-stream",
                data: fileContent,
          });
          } catch (error) {
              console.error("Error sending query:", error);
          }
        }

        // After sending, we clear the input box and the picked file.
        setInput("");
        clearFile();

        // Stimulates placeholder AI reply after a short delay
        setTimeout(() => {
            setMessages((messagesArray) => [
                ...messagesArray,
                { role: "ai", text: "This is a placeholder response." },
            ]);
        }, 200);
    };

    /*upload file placeholder */
    const handleFileSelectClick = () => {
        // Get the hidden file input element.
        const fileInputElement = fileRef.current;
        if (fileInputElement) fileInputElement.click();
    };

    /* Runs when the user selects a file from the file window. */
    const onFileChange: React.ChangeEventHandler<HTMLInputElement> = (event) => {
        const file = event.target.files?.[0] || null;
        // Save the selected file in our 'selectedFile' state.
        setSelectedFile(file);

        // If a file is selected, read its content as base64 and store it
        if (file) {
            const reader = new FileReader();
            // onload is triggered when the file is read successfully by readAsDataURL
            reader.onload = () => {
                const base64String = (reader.result as string).split(",")[1]; // Get base64 part
                setFileContent(base64String);
            };
            // Start reading the file and converted to base64
            // The reason that we put readAsDataURL here is that it's asynchronous
            reader.readAsDataURL(file); // Once this is done, reader.onload will be called
        } else {
            setFileContent(""); // Clear file content if no file is selected
        }
    };
    
    // Clears currently selected files
    const clearFile = () => {
        setSelectedFile(null);
        const fileInputElement = fileRef.current;
        if (fileInputElement) fileInputElement.value = ""; // safe clear
    };

  const handleBackendTestClick = async () => {
    try {
      // Send GET request to backend /hello endpoint
      const res = await axios.get(`${API_BASE}/hello`);
      // setResponse updates the state variable 'response' with the message from the backend
      setResponse(res.data.message);
    } catch (error) {
      console.error("Error connecting to backend:", error);
      setResponse("Error connecting to backend");
    }
  };

   return (
    <div className="app-root">
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
                    {message.text && <div className="message-text">{message.text}</div>}
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