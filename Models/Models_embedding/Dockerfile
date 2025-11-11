# Use an official Python image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy all files to the container
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port (FastAPI default)
EXPOSE 8000

# Command to run your app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

