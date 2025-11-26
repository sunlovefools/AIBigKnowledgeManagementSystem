# Register Page (Frontend)

**Navigation:** [Pages overview](../README.md) | [Workspace page](../mainpage/README.md) | [Frontend doc](../../../FRONTEND_README.md) | [Auth API](../../../../backend/app/api/README.md)

This folder contains the registration UI and styles.

## Files
- `Register.tsx`
  - **Non-technical:** The screen users fill out to create an account.
  - **Technical:** Handles email/password/role inputs, client-side validation, and Axios call to `POST {VITE_API_BASE}/auth/register`.
- `Register.css` — styles for the form, buttons, and message banners.

## Behavior highlights
- Validates passwords (length, upper/lower/number/special) before sending.
- On success, shows a message and navigates to `/mainpage`.
- Handles Axios errors separately for API responses vs network failures.
- Includes a password visibility toggle and role dropdown (`user`/`admin`).
  - **Non-technical:** Gives clear feedback if something is wrong; hides/shows the password on request.
  - **Technical:** Regex check before submit; Axios error branches distinguish response vs network issues.

## Related systems
- Backend auth endpoints live in `backend/app/api/README.md` and are powered by `auth_service.py`.
- Routing shell is defined in `src/App.tsx`.

Keep this README updated if the payload, validation rules, or navigation targets change.
