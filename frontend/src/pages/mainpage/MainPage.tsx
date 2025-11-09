import React, { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import "./MainPage.css";

// blueprint for a chat message, must have a role (user/AI), might have a filename.
type ChatMessage = { role: "user" | "ai"; text: string; fileName?: string };

export default function MainPage() {
    // function to change the page URL, can use it for logging out.
    const navigate = useNavigate();

    // chat state
    // 'messages' is an array that will hold all of our chat message objects.
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    // 'input' will hold the text that the user is currently typing in the textbox.
    const [input, setInput] = useState("");
    // 'picked' will hold the file that the user has selected.
    const [picked, setPicked] = useState<File | null>(null);
    // 'fileRef' gives us a way to "click" the hidden file input element.
    const fileRef = useRef<HTMLInputElement | null>(null);
    // 'listRef' gives us a way to control the message area, specifically to make it scroll.
    const listRef = useRef<HTMLDivElement | null>(null);

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
    const handleSend = () => {
        const t = input.trim();
        // Do nothing if there's no text AND no file selected.
        if (!t && !picked) return;
        // Create a new message object that includes text and an optional fileName.
        const newMessage: ChatMessage = {
            role: "user",
            text: t,
            fileName: picked ? picked.name : undefined, // If a file is picked, add its name.
        };

        // Add our new message object to the end of the 'messages' array.
        setMessages((m) => [...m, newMessage]);

        // After sending, we clear the input box and the picked file.
        setInput("");
        clearFile();

        // Stimulates placeholder AI reply after a short delay
        setTimeout(() => {
            setMessages((m) => [
                ...m,
                { role: "ai", text: "This is a placeholder response." },
            ]);
        }, 200);
    };

    /*upload file placeholder */
    const onPickClick = () => {
        // Get the hidden file input element.
        const el = fileRef.current;
        if (el) el.click();
    };

    /* Runs when the user selects a file from the file window. */
    const onFileChange: React.ChangeEventHandler<HTMLInputElement> = (e) => {
        const f = e.target.files?.[0] || null;
        // Save the selected file in our 'picked' state.
        setPicked(f);
    };
    
    // Clears currently selected files
    const clearFile = () => {
        setPicked(null);
        const el = fileRef.current;
        if (el) el.value = ""; // safe clear
    };

    return (
        <div className="app-root">
            {/* header */}
            <header className="app-header">
                <div className="app-title">AI </div>
                <div className="header-actions">
                    <div className="user-badge">👤 User</div>
                    <button className="logout-button" onClick={handleLogout}>
                        Logout
                    </button>
                </div>
            </header>


            <div className="chat-container">
                {/* Area where all the messages will be displayed */}
                <div className="messages-area" ref={listRef}>

                    {/*If the messages array is empty, show the entire welcome screen.
                     Otherwise, show the chat messages.*/}
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
                            {messages.map((m, i) => (
                                <div key={i} className={`message ${m.role === "user" ? "user" : "ai"}`}>
                                    <div className="message-avatar">
                                        {m.role === "user" ? "👤" : "🤖"}
                                    </div>
                                    <div className="message-content">
                                        {m.text && <div className="message-text">{m.text}</div>}
                                        {m.fileName && (
                                            <div className="message-file-attachment">
                                                <span>{m.fileName}</span>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                </div>


                <div className="input-area">
                    <div className="input-wrapper">
                        <button className="upload-button" title="Upload file" onClick={onPickClick}>
                            📎
                        </button>
                        <input
                            ref={fileRef}
                            type="file"
                            className="file-input"
                            onChange={onFileChange}
                            accept="image/*,.pdf,.doc,.docx,.txt"
                        />

                        {picked && (
                            <div className="uploaded-file">
                                📄 {picked.name}
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
                            // When the user types, update the 'input' state with the new value.
                            onChange={(e) => {
                                setInput(e.target.value);

                            }}
                        />

                        <button
                            className="send-button"
                            onClick={handleSend}
                            // The button is disabled if there's no text AND no file picked.
                            disabled={!input.trim()}
                            title="Send"
                        >
                            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                                <path d="M3.4 20.4L20.85 12.02L3.4 3.6V10.29L15.3 12.02L3.4 13.75V20.4Z" fill="white"/>
                            </svg>
                        </button>


                    </div>
                </div>
            </div>
        </div>
    );
}
