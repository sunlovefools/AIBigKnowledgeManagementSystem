# ---- Base Image ----
FROM python:3.11-slim

# ---- System Setup ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl build-essential && \
    rm -rf /var/lib/apt/lists/*

# ---- Working Directory ----
WORKDIR /app

# ---- Copy and Install Dependencies ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Copy Application Code ----
COPY . .

# ---- Expose Port for FastAPI ----
EXPOSE 8000

# ---- Run the App ----
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
