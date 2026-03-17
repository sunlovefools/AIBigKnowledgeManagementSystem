"""
View chat messages stored in Astra DB via TERMINAL.
!!!USED FOR TESTING AND DEBUGGING ONLY - NOT AN API ENDPOINT!!!
Usage: python scripts/view_chat_messages.py
"""

import os
from dotenv import load_dotenv
from astrapy import DataAPIClient

load_dotenv()

def view_chat_messages():
    # Connect to chat database
    endpoint = os.environ.get("ASTRA_CHAT_DB_URL")
    token = os.environ.get("ASTRA_CHAT_DB_TOKEN")
    keyspace = os.environ.get("ASTRA_CHAT_KEYSPACE")
    collection_name = os.environ.get("ASTRA_CHAT_COLLECTION", "chat_messages")
    
    if not endpoint or not token:
        print("❌ Missing ASTRA_CHAT_DB_URL or ASTRA_CHAT_DB_TOKEN")
        return
    
    print(f"🔌 Connecting to Astra DB...")
    print(f"   Keyspace: {keyspace}")
    print(f"   Collection: {collection_name}\n")
    
    client = DataAPIClient()
    database = client.get_database(endpoint, token=token, keyspace=keyspace)
    collection = database.get_collection(collection_name)
    
    # Get all messages (limit to recent 50)
    print("Fetching recent chat messages...\n")
    messages = list(collection.find({}, limit=50))
    
    if not messages:
        print("!!!No messages found in collection.")
        print("   Try sending a chat message from the frontend first!")
        return
    
    print(f"✅ Found {len(messages)} messages:\n")
    print("=" * 80)
    
    # Group by conversation
    conversations = {}
    for msg in messages:
        conv_id = msg.get("conversationId", "unknown")
        if conv_id not in conversations:
            conversations[conv_id] = []
        conversations[conv_id].append(msg)
    
    # Display each conversation
    for conv_id, msgs in conversations.items():
        print(f"\nConversation: {conv_id}")
        print(f"   User: {msgs[0].get('userEmail', 'unknown')}")
        print(f"   Messages: {len(msgs)}")
        print("-" * 80)
        
        for msg in sorted(msgs, key=lambda x: x.get("timestamp", "")):
            role = msg.get("role", "?")
            text = msg.get("text", "")
            timestamp = msg.get("timestamp", "")
            
            # Truncate long messages
            display_text = text[:100] + "..." if len(text) > 100 else text
            
            icon = "👤" if role == "user" else "🤖"
            print(f"   {icon} [{role.upper()}] {display_text}")
            print(f"      Time: {timestamp}")
        
        print()
    
    print("=" * 80)
    print(f"\n✅ Total conversations: {len(conversations)}")
    print(f"✅ Total messages: {len(messages)}")
    
    # Show sample document structure
    if messages:
        print("\n Sample document structure:")
        sample = messages[0]
        print(f"   _id: {sample.get('_id')}")
        print(f"   conversationId: {sample.get('conversationId')}")
        print(f"   userEmail: {sample.get('userEmail')}")
        print(f"   role: {sample.get('role')}")
        print(f"   text: {sample.get('text')[:50]}...")
        print(f"   timestamp: {sample.get('timestamp')}")


if __name__ == "__main__":
    try:
        view_chat_messages()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise
