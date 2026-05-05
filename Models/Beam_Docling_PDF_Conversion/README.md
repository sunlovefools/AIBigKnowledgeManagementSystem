# Beam Docling Endpoint Service

Dedicated folder for serving `beam_docling_endpoint.py` as an independent `uv` project.

## What "own uv" means

- `uv` (the CLI binary) is usually installed once on your machine.
- This folder can still have its own isolated environment and dependency lock:
  - local virtualenv: `beam_endpoint_service/.venv`
  - local lockfile: `beam_endpoint_service/uv.lock` (generated when you run `uv lock` / `uv sync`)

This gives you a self-contained dependency setup for the Beam endpoint service.

## Setup with `uv` (Windows PowerShell)

```powershell
cd beam_endpoint_service

# If uv is not installed yet (one-time machine setup):
# powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Create/use a project-local venv (Python version is pinned by .python-version)
uv venv .venv

# Install all dependencies from pyproject.toml into the local venv
uv sync

# Start the Beam endpoint using the local environment
uv run beam serve beam_docling_endpoint.py:convert_pdf
```

## Setup with `uv` (bash)

```bash
cd beam_endpoint_service

# If uv is not installed yet (one-time machine setup):
# curl -LsSf https://astral.sh/uv/install.sh | sh

uv venv .venv
uv sync
uv run beam serve beam_docling_endpoint.py:convert_pdf
```

## Optional: activate the local venv manually

If you prefer activating the environment yourself instead of `uv run`:

```powershell
cd beam_endpoint_service
.\.venv\Scripts\Activate.ps1
beam serve beam_docling_endpoint.py:convert_pdf
```

## Notes

- Use the URL printed by the current Beam serve session (do not reuse stale URLs).
- If `uv sync` fails because your network is restricted, run it later on a machine/network with package index access.
- The endpoint returns `page_no` + `bbox` so the client can crop images locally.
- The Beam container image installs Linux system libraries required by Docling/OpenCV (`libGL.so.1` and related runtime libs).
