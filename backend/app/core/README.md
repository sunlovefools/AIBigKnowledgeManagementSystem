# Core Utilities

**Navigation:** [App overview](../README.md) | [API routers](../api/README.md) | [Service layer](../service/README.md) | [Backend docs](../../docs/README.md)

Shared helpers for validation and security live here.

## Modules
- `validation.py`
  - **Non-technical:** Checks that emails and passwords look sensible before we accept them.
  - **Technical:** Email/password validators and sanitizers used by auth flows.
- `password_utils.py`
  - **Non-technical:** Locks your password in a safe so it’s never stored in plain text.
  - **Technical:** Bcrypt hashing and verification utilities.

## Usage
- Authentication flows import both modules via `auth_service.py`.
- Validation errors raise `AuthenticationError` (in the service layer) so routers can map them to HTTP responses.

If you add new cross-cutting helpers, document them here so other teams know where to import common logic.
