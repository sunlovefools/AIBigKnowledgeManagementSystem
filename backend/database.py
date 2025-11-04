import os
from astrapy.data_types import DataAPIVector
from dotenv import load_dotenv
from astrapy import DataAPIClient
from astrapy.info import (
    CreateTableDefinition,
    ColumnType,
    TableKeyValuedColumnType,
    TableKeyValuedColumnTypeDescriptor,
    TableScalarColumnTypeDescriptor,
    TableValuedColumnTypeDescriptor,
    TableVectorColumnTypeDescriptor,
    TableValuedColumnType,
    TablePrimaryKeyDescriptor,
)

# Load environment variables
load_dotenv()

# Get an existing database
client = DataAPIClient()
# Read configuration from environment variables (don't expose sensitive info in code)
ASTRA_DB_URL = os.getenv("ASTRA_DB_URL")
ASTRA_DB_TOKEN = os.getenv("ASTRA_DB_TOKEN")

# Validate required environment variables
if not ASTRA_DB_URL or not ASTRA_DB_TOKEN:
    raise ValueError(
        "❌ Missing required environment variables!\n"
        "Please set in backend/.env file:\n"
        "  - ASTRA_DB_URL\n"
        "  - ASTRA_DB_TOKEN"
    )

database = client.get_database(ASTRA_DB_URL, token=ASTRA_DB_TOKEN)

table_definition = CreateTableDefinition(
    # Define all of the columns in the table
    columns={
        "id": TableScalarColumnTypeDescriptor(column_type=ColumnType.INT),
        "title": TableScalarColumnTypeDescriptor(column_type=ColumnType.TEXT),
        "author": TableScalarColumnTypeDescriptor(column_type=ColumnType.TEXT),
        "content_vector": TableVectorColumnTypeDescriptor(
            dimension=3  # Vector dimension must match your data
        ),
        "number_of_pages": TableScalarColumnTypeDescriptor(column_type=ColumnType.INT),
        "rating": TableScalarColumnTypeDescriptor(column_type=ColumnType.FLOAT),
        "genres": TableValuedColumnTypeDescriptor(
            column_type=TableValuedColumnType.SET,
            value_type=ColumnType.TEXT,
        ),
        "metadata": TableKeyValuedColumnTypeDescriptor(
            column_type=TableKeyValuedColumnType.MAP,
            key_type=ColumnType.TEXT,
            value_type=ColumnType.TEXT,
        ),
        "is_checked_out": TableScalarColumnTypeDescriptor(
            column_type=ColumnType.BOOLEAN
        ),
        "due_date": TableScalarColumnTypeDescriptor(column_type=ColumnType.DATE),
    },
    # Define the primary key for the table.
    # Using id as primary key instead of title
    primary_key=TablePrimaryKeyDescriptor(partition_by=["id"], partition_sort={}),
)


try:
    table = database.create_table(
        "books_table",  # Changed table name to avoid conflict with existing table
        definition=table_definition,
    )
    print("✅ Table created successfully")
except Exception as e:
    # Check if the error is due to table already existing
    error_msg = str(e).lower()
    if "already exists" in error_msg or "cannot_add_existing_table" in error_msg:
        table = database.get_table("books_table")
        print("ℹ️  Table already exists, using existing table")
    else:
        print(f"❌ Failed to create table: {e}")
        raise


table = database.get_table("books_table")
books_data = [
    {
        "id": 1,
        "title": "Vector Search Guide",
        "author": "Jane Smith",
        "content_vector": DataAPIVector([0.1, 0.8, -0.3])
    },
    {
        "id": 2,
        "title": "Database Design",
        "author": "John Doe",
        "content_vector": DataAPIVector([0.5, -0.2, 0.9])
    },
    {
        "id": 3,
        "title": "AI Fundamentals",
        "author": "Alice Johnson",
        "content_vector": DataAPIVector([-0.4, 0.6, 0.1])
    }
]

result = table.insert_many(books_data)
print(f"Successfully loaded {len(result.inserted_ids)} books")