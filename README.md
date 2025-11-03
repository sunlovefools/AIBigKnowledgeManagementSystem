# COMP2002 Group 44 – CGI AI Big Knowledge Management

Welcome to the COMP2002 Software Engineering Project repository by **Team 44**.  
This project is developed in collaboration with **CGI**, focusing on building an **AI-powered Knowledge Management System** that leverages modern cloud and AI technologies.

---

## Project Overview

**AI Big Knowledge Management** is an intelligent, cloud-hosted platform designed to help users upload, manage, and query large collections of documents using Large Language Models (LLMs).  
The system overcomes token limitations in LLMs through vector databases and Retrieval-Augmented Generation (RAG) techniques, enabling the AI to give accurate, context-aware answers based on stored documents rather than its original training data.

### Key Features
- Document upload interface – Users can upload and track document ingestion progress.
- Vector storage – Documents are embedded and stored in a vector database for efficient retrieval.
- Intelligent querying – Users can perform natural language queries with LLM-generated answers grounded in their documents.
- Secure cloud hosting – The system runs on a hosted web platform.
- Scalable architecture – Modular design that supports integration with different AI models and databases.

---

## System Architecture

| Layer | Description |
|-------|--------------|
| Frontend (React + TypeScript) | Provides the user interface for authentication, document upload, and query interaction. |
| Backend (FastAPI) | Acts as the API gateway, handling user requests, authentication (JWT), and orchestration. |
| Document Ingestion Service | Handles background processing, chunking, and embedding generation. |
| Vector Database (AstraDB / Qdrant) | Stores embeddings for semantic search and retrieval. |
| Object Storage (MinIO) | Manages raw document files securely. |
| LLM Orchestrator (LangChain + RAG) | Combines retrieved content with user queries to generate relevant responses. |

---

## Team Members

| Name | Email | Student ID |
|------|--------|------------|
| Shao Yiqi | scyys15@nottingham.ac.uk | 20616297 |
| Ronith Varatharaj | psyrv4@nottingham.ac.uk | 20723191 |
| Saphia Ahmed | psysa22@nottingham.ac.uk | 20679803 |
| Aleksandrs Urums | psyau4@nottingham.ac.uk | 20692567 |
| Ng Yoong Shen | hfyyn6@nottingham.ac.uk| 20609660

---

## Getting Started

### 1. Clone the Repository
```bash
git clone git@projects-ssh.cs.nott.ac.uk:comp2002/2025-2026/team44_project.git
cd team44_project
```

### 2. Backend Setup (FastAPI)
```bash
cd backend
```

### (a) Create and activate a virtual environment

#### Windows:

```bash
python -m venv venv
venv\Scripts\activate
```

#### macOS/Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```


### (b) Install dependencies
```bash
pip install -r requirements.txt
```

### (c) Run the FastAPI development server
```bash
uvicorn main:app --reload
```

Once the server starts, the backend will be running locally at:

http://127.0.0.1:8000

You can open this URL in your browser to confirm that the FastAPI server is running successfully.

### 3. Frontend Setup (React + TypeScript)

In a separate terminal window:
```bash
cd frontend
npm install
npm run dev
```

Once the development server starts, the frontend will be running locally at:

http://localhost:5173

You can open this URL in your browser to view the web application.