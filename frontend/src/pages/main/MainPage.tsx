import React, { useState } from "react";
import "./MainPage.css";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

const MainPage: React.FC = () => {
  // State for input message and uploaded file
  const [input, setInput] = useState("");
  // Enforce file to be of type File or null (Initialize to NULL)
  const [file, setFile] = useState<File | null>(null);

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    // Check if a file is selected
    if (event.target.files && event.target.files.length > 0) {
      // Update the state with the selected file
      setFile(event.target.files[0]);
    }
  };

  // Handle form submission
  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    console.log("Query:", input);
      try {
        const res = await axios.post(`${API_BASE}/query`, {
          query: input,
        });
      } catch (error) {
        console.error("Error during submission:", error);
      }
    setInput("");
    setFile(null);
  };

  return (
    <div className="main-center-container">
      <h1 className="welcome-text">Welcome to your AI Knowledge System</h1>

      <form onSubmit={handleSubmit} className="center-input-box">
        <label htmlFor="file-upload" className="upload-label">
          ＋
        </label>
        <input
          id="file-upload"
          type="file"
          onChange={handleFileUpload}
          style={{ display: "none" }}
        />

        <input
          type="text"
          placeholder="Send a message..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="text-input"
        />

        <button type="submit" className="send-button">
          ➤
        </button>
      </form>

      {file && <p className="file-info">📎 {file.name}</p>}
    </div>
  );
};

export default MainPage;
