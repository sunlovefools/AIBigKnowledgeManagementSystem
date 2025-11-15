import os
from astrapy import DataAPIClient
from astrapy.info import CollectionVectorOptions, CollectionDefinition

#load Astra credentials from .env files
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

COLLECTION_NAME = "document_chunks_2"

def init_vector_db():
    if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
        raise ValueError("ERROR:Missing Astra DB credentials in .env file")

    client = DataAPIClient(ASTRA_DB_TOKEN)
    db = client.get_database(ASTRA_DB_URL)

    #Check existing collections
    existing = db.list_collection_names()
    if COLLECTION_NAME in existing: #IF EXIST ALREADY, RETURN EXISTSING COLLECTION OBJECT
        print(f"✅ vector collection '{COLLECTION_NAME}' already exists.")
        return db.get_collection(COLLECTION_NAME)

    print(f"creating vector collection '{COLLECTION_NAME}' ...")

    collection_definition = CollectionDefinition(
        vector= CollectionVectorOptions(
            dimension=768,
            metric="cosine"
        )
    )
    #CREATE COLLECTION WITH SCHEMA IF NOT EXIST
    collection = db.create_collection(
        COLLECTION_NAME,
        definition=collection_definition
    )

    print(f"✅ created vector collection '{COLLECTION_NAME}' successfully.")
    return collection
