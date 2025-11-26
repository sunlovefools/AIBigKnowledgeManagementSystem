# Pages Overview

**Navigation:** [Frontend doc](../FRONTEND_README.md) | [Register page](./register/README.md) | [Workspace page](./mainpage/README.md) | [Backend APIs](../../../backend/app/api/README.md)

This folder groups page-level React components. Each subfolder holds a page component plus its styles.

## Current pages
- `register/`
  - **Non-technical:** Sign-up screen that collects email, password, and role.
  - **Technical:** Calls `/auth/register` via Axios using `VITE_API_BASE`.
- `mainpage/`
  - **Non-technical:** Chat-style workspace for asking questions and uploading documents.
  - **Technical:** Calls `/api/query` for answers and `/ingest/webhook` for uploads.

Add new pages as subfolders with their own README to keep feature ownership clear.
