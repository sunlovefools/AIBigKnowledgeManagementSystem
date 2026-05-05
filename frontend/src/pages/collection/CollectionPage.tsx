import { useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import GlobalSidebar from "../../components/GlobalSidebar";
import { saveConversationLaunch } from "../mainpage/conversationLaunch";
import { useDocuments } from "../mainpage/hooks/documents/useDocuments";
import "./CollectionPage.css";

export default function CollectionPage() {
    const navigate = useNavigate();
    const {
        collections,
        isLoadingCollections,
        collectionError,
        createNewCollection,
    } = useDocuments();

    const [isCreateOpen, setIsCreateOpen] = useState(false);
    const [newCollectionName, setNewCollectionName] = useState("");
    const [collectionActionError, setCollectionActionError] = useState<string | null>(null);
    const [collectionActionInfo, setCollectionActionInfo] = useState<string | null>(null);
    const [isCreatingCollection, setIsCreatingCollection] = useState(false);
    const [input, setInput] = useState("");

    const handleSend = () => {
        const prompt = input.trim();
        if (!prompt) return;

        // The collection landing page only launches conversations; the chat turn
        // is executed by /conversation so answers always use the full chat UI.
        saveConversationLaunch({
            prompt,
            scope: { type: "all_collections" },
        });
        navigate("/conversation");
    };

    const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            handleSend();
        }
    };

    const handleCreateCollection = async () => {
        const trimmed = newCollectionName.trim();
        if (!trimmed) {
            setCollectionActionError("Collection name must not be empty.");
            return;
        }
        const duplicate = collections.find(
            (entry) => entry.name.trim().toLowerCase() === trimmed.toLowerCase()
        );
        if (duplicate) {
            setCollectionActionError(`A collection named "${duplicate.name}" already exists.`);
            return;
        }

        setIsCreatingCollection(true);
        setCollectionActionError(null);
        setCollectionActionInfo(null);
        const result = await createNewCollection(trimmed);
        setIsCreatingCollection(false);

        if (!result.ok) {
            setCollectionActionError(result.error ?? "Failed to create collection.");
            return;
        }

        setCollectionActionInfo(`Collection "${trimmed}" created.`);
        setNewCollectionName("");
        setIsCreateOpen(false);
    };

    return (
        <div className="collection-page-shell">
            <GlobalSidebar mode="collection" />

            <main className="collection-page-main">
                <section className="collection-chat-stage">
                    <div className="collection-chat-copy">
                        <div className="collection-chat-eyebrow">Global scope</div>
                        <h1 className="collection-chat-title">Ask across your knowledge</h1>
                        <p className="collection-chat-subtitle">
                            Standard search checks all collections you own, then you can enter a specific collection below.
                        </p>
                    </div>

                    <div className="collection-chatbox">
                        <textarea
                            className="collection-chat-input"
                            value={input}
                            onChange={(event) => setInput(event.target.value)}
                            onKeyDown={handleComposerKeyDown}
                            placeholder="Ask anything across all collections..."
                            rows={1}
                        />
                        <button
                            type="button"
                            className="collection-chat-send"
                            onClick={handleSend}
                            disabled={!input.trim()}
                        >
                            Send
                        </button>
                    </div>
                </section>

                <section className="collection-list-stage">
                    <div className="collection-list-header">
                        <h2>Your Collections</h2>
                    </div>

                    {collectionError && <div className="collection-status error">{collectionError}</div>}
                    {collectionActionError && <div className="collection-status error">{collectionActionError}</div>}
                    {collectionActionInfo && <div className="collection-status info">{collectionActionInfo}</div>}

                    <div className="collection-grid">
                        <button
                            type="button"
                            className="collection-card collection-add-card"
                            onClick={() => {
                                setIsCreateOpen((previous) => !previous);
                                setCollectionActionError(null);
                                setCollectionActionInfo(null);
                            }}
                        >
                            <span className="collection-add-plus" aria-hidden="true">+</span>
                            <span className="collection-card-title">New Collection</span>
                            <span className="collection-card-subtitle">Create a workspace and add files</span>
                        </button>

                        {isCreateOpen && (
                            <div className="collection-card collection-create-card">
                                <label className="collection-create-label" htmlFor="new-collection-name">
                                    Collection name
                                </label>
                                <input
                                    id="new-collection-name"
                                    className="collection-create-input"
                                    type="text"
                                    value={newCollectionName}
                                    onChange={(event) => setNewCollectionName(event.target.value)}
                                    maxLength={120}
                                    placeholder="e.g. Product docs"
                                    disabled={isCreatingCollection}
                                />
                                <div className="collection-create-actions">
                                    <button
                                        type="button"
                                        className="collection-create-btn primary"
                                        onClick={() => { void handleCreateCollection(); }}
                                        disabled={isCreatingCollection}
                                    >
                                        {isCreatingCollection ? "Creating..." : "Create"}
                                    </button>
                                    <button
                                        type="button"
                                        className="collection-create-btn"
                                        onClick={() => setIsCreateOpen(false)}
                                        disabled={isCreatingCollection}
                                    >
                                        Cancel
                                    </button>
                                </div>
                            </div>
                        )}

                        {isLoadingCollections && collections.length === 0 ? (
                            <div className="collection-status">Loading collections...</div>
                        ) : collections.length === 0 ? (
                            <div className="collection-status">No collections available yet.</div>
                        ) : (
                            collections.map((collection) => (
                                <button
                                    key={collection.collectionId}
                                    type="button"
                                    className="collection-card"
                                    onClick={() => {
                                        saveConversationLaunch({
                                            scope: {
                                                type: "collection",
                                                collectionId: collection.collectionId,
                                                collectionName: collection.name,
                                            },
                                        });
                                        navigate("/conversation");
                                    }}
                                >
                                    <div className="collection-card-title">{collection.name}</div>
                                    <div className="collection-card-subtitle">
                                        {collection.isDefault ? "Default collection" : "Custom collection"}
                                    </div>
                                    <div className="collection-card-meta">{collection.fileCount} file(s)</div>
                                </button>
                            ))
                        )}
                    </div>
                </section>
            </main>
        </div>
    );
}
