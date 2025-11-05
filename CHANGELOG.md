# Changelog

All notable changes to this project will be documented in this file.



## Unreleased

### Added
- Features that have been added but not yet released

### Changed
- Changes in existing functionality

### Deprecated
- Features that will be removed in upcoming releases

### Removed
- Features that have been removed

### Fixed
- Bug fixes

### Security
- Security improvements or vulnerability fixes

---

## 1.0.0 - 2025-11-05

### Added
Author: @psysa22
- Login react page with comments
- Created a pages folder within the SRC to hold the login and register pages
- Added CSS style sheet for LoginPage

### Modified
Author: @psysa22
- Changed main.tsx to run LoginPage instead of App.tsx (have commented out lines for App.tsx)

### Technical Details
Author: @psysa22
- Framework: React 18 + TypeScript
- Build Tool: Vite
- Styling: CSS (inline and external stylesheets)
- HTTP Requests: Fetch API
- Form Validation: Regex-based client-side validation
- State Management: React useState hooks
- Routing (optional, if added later): React Router DOM
- Node.js version: v18+ recommended

## 1.0.0 - 2025-11-04

### Added
- User authentication system with secure password hashing
- Email validation and duplicate account detection
- Password strength requirements enforcement
- User registration functionality
- User login with credential verification
- Bcrypt password hashing implementation
- Email format validation using email-validator
- Account status tracking (active/inactive)
- Complete test suite with 10 test cases
- Authentication service API (`AuthService` class)
- Database schema for user management
- Comprehensive documentation (AUTH_README.md, SUMMARY_CN.md)

### Security
- Implemented bcrypt for secure password hashing
- Added password strength validation (8+ chars, uppercase, lowercase, digit, special char)
- Protected against user enumeration attacks with generic error messages
- Automatic salt generation for each password
- Timezone-aware timestamp storage

### Technical Details
- Database: Astra DB
- Password Hashing: bcrypt
- Email Validation: email-validator library
- Python version: 3.11+

---

## [0.1.0] - 2025-11-04

### Added
- Initial project setup
- Database connection configuration
- Environment variable management with python-dotenv
- Basic project structure

### Changed
- Reorganized database files into subdirectory
- Updated requirements.txt with new dependencies

---



