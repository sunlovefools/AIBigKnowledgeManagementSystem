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
| Ng Yoong Shen | hfyyn6@nottingham.ac.uk| 20609660|
| Yan Sin Lee | psyyl13@nottingham.ac.uk| 20684067|

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
uvicorn app.main:app --reload
```

Once the server starts, the backend will be running locally at:

http://127.0.0.1:8000

You can open this URL in your browser to confirm that the FastAPI server is running successfully.

### 3. Frontend Setup (React + TypeScript)

First, you need to install Node.js on your computer.

You can visit the official [Node.js website](https://nodejs.org/en) and then install Node.js.

In a separate terminal window:
```bash
cd frontend
npm install
npm run dev
```

Once the development server starts, the frontend will be running locally at:

http://localhost:5173

You can open this URL in your browser to view the web application.

---

## Backend Architecture

Our backend follows a **layered architecture pattern** for clean separation of concerns and maintainability. The system is built with **FastAPI** and uses **Astra DB** as the primary database.

###  Directory Structure

```
backend/
├── app/
│   ├── api/              # HTTP endpoint definitions
│   │   └── router_auth.py
│   ├── core/             # Core utilities and validation
│   │   ├── password_utils.py
│   │   └── validation.py
│   ├── service/          # Business logic layer
│   │   ├── auth_service.py
│   │   └── beam_client.py
│   └── main.py           # FastAPI application entry point
├── tests/                # Unit and integration tests
│   └── test_latest.py
├── .env                  # Environment variables
├── requirements.txt      # Python dependencies
└── SUMMARY.md            # Detailed backend documentation
```

###  Architecture Layers

| Layer | Responsibility | Key Files |
|-------|---------------|-----------|
| **API Layer** | HTTP request/response handling | `api/router_auth.py` |
| **Service Layer** | Business logic and orchestration | `service/auth_service.py` |
| **Core Layer** | Utilities and validation | `core/password_utils.py`, `core/validation.py` |
| **Database Layer** | Data persistence | Astra DB integration |

###  Authentication System

The backend implements a secure user authentication system with the following features:

- **User Registration**: Email validation, password strength enforcement, duplicate detection
- **User Login**: Credential verification with bcrypt password hashing
- **Security**: Secure password storage, input validation, CORS protection

**Available Endpoints:**
- `POST /auth/register` - Register a new user account
- `POST /auth/login` - Authenticate user and return session token
- `GET /hello` - Health check endpoint

###  Technology Stack

| Component | Technology |
|-----------|------------|
| Web Framework | FastAPI |
| Database | Astra DB (Cloud NoSQL) |
| Password Hashing | bcrypt |
| Email Validation | email-validator |
| Testing | pytest |
| Python Version | 3.11+ |

###  Detailed Documentation

For comprehensive backend documentation including API specifications, service methods, and development guidelines, please refer to:

 **[Backend SUMMARY.md](./backend/SUMMARY.md)**

This document provides:
- Complete folder structure breakdown
- API endpoint specifications
- Service layer method signatures
- Testing guidelines
- Performance optimization notes

---

## Frontend Architecture

Our frontend is a modern **Single Page Application (SPA)** built with **React 19** and **TypeScript**, using **Vite** as the build tool for fast development and optimized production builds.

###  Directory Structure

```
frontend/
├── src/
│   ├── pages/            # Page components
│   │   └── register/     # User registration module
│   │       ├── Register.tsx
│   │       └── Register.css
│   ├── assets/           # Static assets (images, icons)
│   ├── App.tsx           # Root component
│   ├── App.css           # Root component styles
│   ├── main.tsx          # Application entry point
│   ├── index.css         # Global styles
│   └── vite-env.d.ts     # TypeScript environment definitions
├── public/               # Public static files
├── .env.development      # Development environment variables
├── .env.production       # Production environment variables
├── vite.config.ts        # Vite configuration
├── tsconfig.json         # TypeScript configuration
├── package.json          # Dependencies and scripts
└── index.html            # HTML entry point
```

### Component Architecture

```
App (Root Component)
├── Backend Connection Test
│   └── Button to test API connectivity
└── Register Component
    ├── Email Input (with validation)
    ├── Password Input (with strength validation)
    │   └── Password Visibility Toggle
    ├── Role Selection (User/Admin)
    └── Submit Button
```

###  UI Features

| Feature | Description | Implementation |
|---------|-------------|----------------|
| **Form Validation** | Client-side input validation | HTML5 + Custom regex |
| **Password Strength** | 8+ chars, uppercase, lowercase, digit, special char | Real-time validation |
| **Password Toggle** | Show/hide password functionality | SVG icons with state management |
| **Error Feedback** | Success/error messages with color coding | Conditional CSS classes |
| **API Integration** | Axios-based HTTP client | Environment-aware base URL |
| **Responsive Design** | Mobile-friendly layout | CSS flexbox + media queries |

###  Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| UI Framework | React | 19.1.1 |
| Language | TypeScript | 5.9.3 |
| Build Tool | Vite | 7.1.7 |
| HTTP Client | Axios | 1.13.1 |
| Code Quality | ESLint | 9.36.0 |
| Styling | CSS3 | - |

###  Environment Configuration

The frontend uses environment variables to configure API endpoints for different deployment environments:

```typescript
// Development (.env.development)
VITE_API_BASE=http://18.175.57.152:8000

// Production (.env.production)
VITE_API_BASE=https://ec2-18-175-57-26.eu-west-2.compute.amazonaws.com


**Usage in code:**
```typescript
const API_BASE = import.meta.env.VITE_API_BASE;
```

###  API Integration

**Current Implemented Features:**

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `/hello` | GET | Backend connectivity test | ✅ Implemented |
| `/auth/register` | POST | User registration | ✅ Implemented |
| `/auth/login` | POST | User login | ⏳ Backend ready, frontend pending |

**Request Example (Registration):**
```typescript
const response = await axios.post(`${API_BASE}/auth/register`, {
  email: "user@example.com",
  password: "SecurePass123!",
  role: "user"
});
```

###  Build & Deployment

**Development:**
```bash
npm run dev          # Start dev server on http://localhost:5173
```

**Production Build:**
```bash
npm run build        # Build for production (outputs to dist/)
npm run preview      # Preview production build locally
```

**GitHub Pages Deployment:**
- Automated via GitHub Actions (`.github/workflows/deploy.yml`)
- Base path configured for GitHub Pages: `/AIBigKnowledgeManagementSystem/`
- Production API URL automatically injected from GitHub Secrets

###  Current Functionality

** Implemented:**
- Backend health check and connectivity test
- User registration with complete validation
- Password strength enforcement
- Email format validation
- Role selection (User/Admin)
- Real-time error feedback
- Responsive form design

**⏳ Planned Features:**
- User login interface
- JWT token management
- Protected routes
- User dashboard
- Document upload interface
- Query interface for AI interactions
- User profile management

### Security Features

- **Client-side Validation**: Prevents invalid data submission
- **HTTPS Communication**: Encrypted data transmission in production
- **CORS Configuration**: Backend restricts origins to deployed frontend
- **Password Requirements**: Enforced strength rules
- **XSS Protection**: React's built-in escaping

###  Detailed Documentation

For additional frontend development information including Vite configuration, React Compiler setup, and ESLint customization, please refer to:

 **[Frontend README.md](./frontend/README.md)**

This document provides:
- Vite + React setup details
- Hot Module Replacement (HMR) configuration
- ESLint type-aware rules configuration
- React-specific linting plugins
- Development best practices

---

## Deployment 

### DataBase
Our (vector)database is [Astra](https://www.ibm.com/products/datastax)

### FrontEnd
Our frontend is now deployed on [GitHub](https://sunlovefools.github.io/AIBigKnowledgeManagementSystem/).

### Backend
Our backend is now deployed on [AWS EC2](https://aws.amazon.com/). 