# Deployment Guide

This project deploys the frontend as a Vite static site on GitHub Pages and the backend as a Dockerized FastAPI service.

## Frontend: GitHub Pages

The workflow is `.github/workflows/deploy.yml`. It runs on pushes to `main` and can also be started manually from the GitHub Actions tab.

Required GitHub repository secret:

| Name | Example | Purpose |
| --- | --- | --- |
| `VITE_API_BASE` | `https://ec2-18-175-57-26.eu-west-2.compute.amazonaws.com` | Backend API base URL used by the built frontend. |

Optional GitHub repository secrets:

| Name | Purpose |
| --- | --- |
| `VITE_AUTH0_DOMAIN` | Auth0 tenant domain if Auth0 UI is enabled. |
| `VITE_AUTH0_CLIENT_ID` | Auth0 SPA client id. |
| `VITE_AUTH0_AUDIENCE` | Auth0 API audience. |

Optional GitHub repository variables:

| Name | Default | Purpose |
| --- | --- | --- |
| `VITE_BASE_PATH` | `/AIBigKnowledgeManagementSystem/` | GitHub Pages base path. |
| `VITE_AUTH0_REDIRECT_URI` | `https://sunlovefools.github.io/AIBigKnowledgeManagementSystem/login` | Auth0 login redirect URI. |
| `VITE_AUTH0_LOGOUT_RETURN_TO` | `https://sunlovefools.github.io/AIBigKnowledgeManagementSystem/` | Auth0 logout return URL. |

GitHub Pages must be configured to deploy from GitHub Actions:

1. Open repository Settings.
2. Go to Pages.
3. Set Source to `GitHub Actions`.
4. Push to `main` or run `Deploy Vite app to GitHub Pages`.

### Auth0 and Mobile Testing

Auth0's SPA SDK only runs on secure origins: `https://`, `http://localhost`, or `http://127.0.0.1`.

That means a phone cannot complete Auth0 login against a Vite dev server opened through a local network URL such as:

```text
http://10.249.67.204:5173/login
```

The app shows a clear notice instead of crashing on those local network URLs. To test the real mobile login flow, deploy the frontend to HTTPS and add the deployed callback URL in Auth0:

```text
https://sunlovefools.github.io/AIBigKnowledgeManagementSystem/login
```

Also add the same deployed origin to Auth0's allowed logout URLs and web origins where applicable.

## Backend: Docker Compose

Create `backend/.env` on the server with the required backend secrets and production CORS origins:

```env
CORS_ORIGINS=http://localhost:5173,https://sunlovefools.github.io
ASTRA_DB_URL=...
ASTRA_DB_TOKEN=...
BEAM_LLM_URL=...
BEAM_LLM_KEY=...
```

Then deploy from the repository root:

```bash
docker compose up -d --build
docker compose ps
```

The backend health check calls `GET /hello` from inside the container.

## AWS Role

GitHub Pages can only host the static frontend. The FastAPI backend must run somewhere with a public network address; in this project that target is AWS EC2.

The EC2 instance needs:

- Docker and Docker Compose installed.
- A checked-out copy of this repository.
- A populated `backend/.env` file.
- A security group that allows inbound traffic to the backend port or to the HTTPS reverse proxy.
- A production `CORS_ORIGINS` value that includes the GitHub Pages origin.

For a browser-accessible production setup, prefer HTTPS in front of the backend. A GitHub Pages HTTPS frontend calling a plain HTTP backend can be blocked by browser mixed-content rules.
