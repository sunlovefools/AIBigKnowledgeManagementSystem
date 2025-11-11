# ---- Base image ----
FROM python:3.11-slim

# ---- Prevent Python from buffering output ----
ENV PYTHONUNBUFFERED=1

# ---- Set working directory ----
WORKDIR /app

# ---- Install system dependencies ----
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# ---- Copy dependency files first (for caching) ----
COPY requirements.txt .

# ---- Install Python dependencies ----
RUN pip install --no-cache-dir -r requirements.txt

# ---- Copy the rest of the app ----
COPY . .

# ---- Expose FastAPI port ----
EXPOSE 8000

# ---- Command to run the API ----
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
