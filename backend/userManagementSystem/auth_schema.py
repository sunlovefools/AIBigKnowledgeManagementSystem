"""
User authentication schema definition
"""
from astrapy.info import (
    CreateTableDefinition,
    ColumnType,
    TableScalarColumnTypeDescriptor,
    TablePrimaryKeyDescriptor,
)

# Define the users table schema
def get_users_table_definition():
    """Define the users table schema for authentication"""
    # Create a table with their associated columns and types of each column
    # This is shouldn't be something that we should use at all because we will assume that we have this table created
    return CreateTableDefinition(
        columns={
            "id": TableScalarColumnTypeDescriptor(column_type=ColumnType.INT),
            "email": TableScalarColumnTypeDescriptor(column_type=ColumnType.TEXT),
            "password_hash": TableScalarColumnTypeDescriptor(column_type=ColumnType.TEXT),
            "created_at": TableScalarColumnTypeDescriptor(column_type=ColumnType.TIMESTAMP),
            "is_active": TableScalarColumnTypeDescriptor(column_type=ColumnType.BOOLEAN),
        },
        primary_key=TablePrimaryKeyDescriptor(partition_by=["id"], partition_sort={}),
    )
