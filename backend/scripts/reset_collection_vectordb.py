# This script is used to reset (clear) all the vector database collections.
# Delete the existing collections used for RAG storage in Astra DB.
# On next backend startup, the collections will be recreated empty.
# So after running this script, restart the backend server.

import os
from dotenv import load_dotenv
from astrapy import DataAPIClient

# 1. Load environment variables
load_dotenv()

ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

# 2. Define the collection names used in your app
# These match the constants in app/vectordb/vectordb_init.py
CHILD_COLLECTION_NAME = "rag_child_vectors"       # Stores the vector embeddings
PARENT_COLLECTION_NAME = "rag_parent_documents"   # Stores the full text content

def reset_database():
    """
    Drops the vector and document collections to clear all data.
    """
    if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
        raise ValueError("❌ Missing Astra DB credentials in .env file")

    print("🔌 Connecting to Astra DB...")
    
    # Initialize the client (Pattern matches app/service/auth_service.py)
    client = DataAPIClient()
    database = client.get_database(ASTRA_DB_URL, token=ASTRA_DB_TOKEN)
    
    print(f"✅ Connected to database: {database.info().name}\n")

    # 3. Drop the collections
    # Note: app/vectordb/vectordb_init.py will automatically recreate these
    # when you restart the backend application.
    collections_to_drop = [CHILD_COLLECTION_NAME, PARENT_COLLECTION_NAME]

    for collection_name in collections_to_drop:
        try:
            print(f"🗑️  Attempting to drop collection: '{collection_name}'...")
            database.drop_collection(collection_name)
            print(f"✅ Successfully dropped '{collection_name}'.")
        except Exception as e:
            # It's okay if it fails if the collection doesn't exist yet
            print(f"⚠️  Could not drop '{collection_name}' (it might not exist): {e}")

    print("\n✨ Database reset complete. Restart your backend to recreate empty collections.")

if __name__ == "__main__":
    # Add a simple confirmation prompt to prevent accidents
    confirm = input("⚠️  This will DELETE ALL data in your RAG vector database. Are you sure? (y/n): ")
    if confirm.lower() == 'y':
        reset_database()
    else:
        print("❌ Operation cancelled.")