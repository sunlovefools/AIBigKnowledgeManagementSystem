import { useEffect, useState } from "react";
import axios from "axios";
import "./ModificationModal.css";

interface DocumentInfo {
  id: string;
  fileName: string;
  content: string;
  size: number;
  chunks: number;
}

interface ModificationModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");

export default function ModificationModal({ isOpen, onClose }: ModificationModalProps) {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<DocumentInfo | null>(null);

  // Fetch documents when modal opens
  useEffect(() => {
    if (isOpen) {
      fetchDocuments();
    }
  }, [isOpen]);

  const fetchDocuments = async () => {
    setIsLoading(true);
    setError(null);
    setDocuments([]);

    try {
      const response = await axios.get(`${API_BASE}/api/modifications/list`);
      setDocuments(response.data.documents);
    } catch (err) {
      console.error("Failed to fetch documents:", err);
      setError("Failed to retrieve documents. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">Modifications</h2>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="modal-body">
          {isLoading && (
            <div className="loading-state">
              <div className="spinner"></div>
              <p>Loading documents...</p>
            </div>
          )}

          {error && (
            <div className="error-state">
              <p className="error-message">{error}</p>
              <button className="retry-btn" onClick={fetchDocuments}>
                Retry
              </button>
            </div>
          )}

          {!isLoading && !error && documents.length === 0 && (
            <div className="modifications-list">
              <p className="empty-state">No documents available yet.</p>
              <p className="empty-hint">Upload files from the left panel to make modifications.</p>
            </div>
          )}

          {!isLoading && !error && documents.length > 0 && !selectedDoc && (
            <div className="modifications-list">
              <p className="list-header">Available Documents ({documents.length})</p>
              {documents.map((doc) => (
                <div key={doc.id} className="modification-item">
                  <div className="item-info">
                    <p className="modification-item-name">{doc.fileName}</p>
                    <p className="modification-item-meta">
                      {doc.size} characters • {doc.chunks} chunks
                    </p>
                  </div>
                  <button
                    className="modification-item-action"
                    onClick={() => setSelectedDoc(doc)}
                  >
                    View
                  </button>
                </div>
              ))}
            </div>
          )}

          {selectedDoc && (
            <div className="document-preview">
              <div className="preview-header">
                <button className="back-btn" onClick={() => setSelectedDoc(null)}>
                  ← Back
                </button>
                <h3>{selectedDoc.fileName}</h3>
              </div>
              <div className="preview-content">
                <pre>{selectedDoc.content}</pre>
              </div>
              <div className="preview-footer">
                <p className="preview-meta">
                  Size: {selectedDoc.size} characters • Chunks: {selectedDoc.chunks}
                </p>
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          {selectedDoc && (
            <>
              <button className="modal-btn modal-btn-primary">
                Edit
              </button>
              <button className="modal-btn modal-btn-secondary" onClick={() => setSelectedDoc(null)}>
                Cancel
              </button>
            </>
          )}
          {!selectedDoc && (
            <button className="modal-btn modal-btn-secondary" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

