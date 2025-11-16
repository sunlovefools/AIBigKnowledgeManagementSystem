import sys
import os

# ================================
# 📌 Add backend directory to sys.path
# ================================
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(CURRENT_DIR)

sys.path.insert(0, BACKEND_DIR)
print("[DEBUG] Added backend directory to sys.path:", BACKEND_DIR)

import asyncio
import json
import httpx
from app.main import app


# ================================
# 🔍 Test `/api/query`
# ================================
async def test_query():
    print("\n=== 🔍 Testing /api/query ===")

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/query",
            json={"query": "What is concurrency in operating systems?", "top_k": 5}
        )

    print("Status Code:", response.status_code)
    print(json.dumps(response.json(), indent=4, ensure_ascii=False))


# ================================
# 🔍 Test `/api/query/direct`
# ================================
async def test_query_direct():
    print("\n=== 🔍 Testing /api/query/direct ===")

    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/query/direct",
            json={"query": "memory segmentation", "top_k": 5}
        )

    print("Status Code:", response.status_code)
    print(json.dumps(response.json(), indent=4, ensure_ascii=False))


# ================================
# ▶️ Run tests
# ================================
if __name__ == "__main__":
    asyncio.run(test_query())
    asyncio.run(test_query_direct())
