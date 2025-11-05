# User Authentication System - Project Summary


##  Backend Folder Structure

```
backend/
│
├── app/
│ ├── api/
│ │ └── router_auth.py
│ │
│ ├── core/
│ │ ├── password_utils.py
│ │ └── validation.py
│ │
│ ├── service/
│ │ ├── auth_service.py
│ │ └── init.py
│ │
│ └── main.py
│
├── tests/
│ └── test_latest.py
│
├── .env
├── pytest.ini
├── requirements.txt
└── SUMMARY.md
```
## 🧩 Folder Breakdown

### 1. `api/`
This layer contains all **API endpoint definitions** that handle HTTP requests and responses.  
Each file here represents a specific feature or route group.

- **`router_auth.py`**  
  Defines the authentication-related endpoints such as:
  - `POST /auth/register` – Register a new user  
  - `POST /auth/login` – Authenticate an existing user and issue a token  

This layer interacts with the `service` layer to perform the actual business logic, keeping routes clean and focused on request handling.

---

### 2. `core/`
This layer contains **fundamental utilities and validation logic** used across the backend.

- **`password_utils.py`**  
  Provides helper functions for password management, such as hashing and verifying passwords securely.

- **`validation.py`**  
  Defines functions for validating user input (e.g., checking email format, password length, and enforcing constraints such as minimum and maximum length).

This ensures all input data is clean and consistent before being processed.

---

### 3. `service/`
This layer contains all **business logic and helper services** that power the API layer.

- **`auth_service.py`**  
  Implements authentication-related services such as user registration, login verification, and token generation.  
  These functions are used by `router_auth.py` to handle user authentication flow.

By keeping the logic here, we achieve better **separation of concerns** — API routes only delegate work to service functions.

---

### 4. `tests/`
Contains all **unit and integration test files**.  
Tests are written using `pytest` to ensure that each component works as expected.

- **`test_latest.py`** – Includes tests for authentication and validation functionality.

To run tests manually:
```bash
# From the backend directory
pytest
```
Make sure pytest is installed, either globally or in your virtual environment.

If not perform this command:
```bash
pip install pytest
```

### 5. Other Files

- **`.env`** – Stores environment variables (e.g., database credentials, JWT secret key).  
- **`requirements.txt`** – Lists all required Python packages for the backend.  
- **`pytest.ini`** – Configures `pytest` settings.  
- **`main.py`** – Entry point for running the FastAPI application.

---


##  Performance Notes

 **Index Warning**: Current implementation queries users by email without an index. For production use:

1. Add index on email column
2. Consider email as part of primary key
3. Implement caching for frequently accessed users


##  Technical Stack

| Component          | Technology      |
|--------------------|-----------------|
| Database           | Astra DB        |
| Password Hashing   | bcrypt          |
| Email Validation   | email-validator |
| Python Version     | 3.11+           |
| Web Framework      | FastAPI         |



## AuthService Class Methods

### Core Authentication Methods

| Method | Parameters | Return Type | Description | Throws |
|--------|-----------|-------------|-------------|---------|
| `register_user()` | `email: str`<br>`password: str` | `Dict[str, Any]` | Register a new user account | `AuthenticationError` |
| `login_user()` | `email: str`<br>`password: str` | `Dict[str, Any]` | Authenticate user login | `AuthenticationError` |
| `email_exists()` | `email: str` | `bool` | Check if email is already registered | - |
| `get_user_by_email()` | `email: str` | `Optional[Dict[str, Any]]` | Retrieve user information by email | - |

---

**Version**: 1.0.0  
**Last Updated**: 2025-11-05  
**Module**: User Management System
