import React, { useState } from "react";
import axios from "axios";
import "./Register.css";

export default function Register() {
    const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");
    // Form state
    // The state we have are: email, password, showPassword, role, message
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false); // By default, password is hidden
    const [role, setRole] = useState("user");
    const [message, setMessage] = useState("");

    // Handle form submission
    const handleRegister = async (e: React.FormEvent) => {
        // Prevent default form submission behavior (page reload)
        e.preventDefault();
        setMessage("");

        // Specifying the password rules using regex
        const rule =
            /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?":{}|<>]).{8,}$/;
        if (!rule.test(password)) {
            return setMessage(
                "Password must be 8+ characters with uppercase, lowercase, number, and special symbol"
            );
        }

        // Send POST request to backend /auth/register endpoint along with email, password, and role
        try {
            // The data structure sent to the backend is:
            // {
            //   email: string,
            //   password: string,
            //   role: string
            // }
            const res = await axios.post(`${API_BASE}/auth/register`, {
                email,
                password,
                role,
            });
            console.log(res.data);
            setMessage("Registered successfully!");
            setEmail("");
            setPassword("");
            setRole("user");
        } catch (error) {
            if (axios.isAxiosError(error)) {
                // 2. Check if the server sent a response (e.g., not a network error)
                if (error.response) {
                    // Now TypeScript knows error.response exists
                    console.log("Error during registration:", error.response.data);

                    // From your previous question, the message is in error.response.data.detail
                    const errorMessage = error.response.data.detail || "Registration failed.";
                    setMessage(errorMessage);
                } else {
                    // This handles network errors where no response was received
                    console.error("Network Error:", error.message);
                    setMessage("Network error. Please try again.");
                }
            } else {
                // This handles non-Axios errors (e.g., a bug in your React code)
                console.error("An unexpected error occurred:", error);
                setMessage("An unexpected error occurred.");
            }
        }
    };

    return (
        <div className="register-container">
            <h2>Register</h2>

            <form onSubmit={handleRegister} className="register-form">
                {/* When the form is submitted , the handleRegister function will be called */}
                {/* The data structure sent to the backend is: */}
                {/* {
                  email: email,
                  password: password,
                  role: role
                } */}
                <input
                    type="email"
                    placeholder="Email"
                    // What does value mean here?
                    // The value prop is used to set the current value of the input field
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    className="register-input"
                    aria-label="Email"
                    autoComplete="email"
                />

                <div className="password-wrapper">
                    <input
                    // If the showPassword state is true, the input type will be "text" to show the password
                        type={showPassword ? "text" : "password"}
                        placeholder="Password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                        className="register-input"
                        aria-label="Password"
                        autoComplete="new-password"
                    />
                    <button
                        type="button"
                        className="toggle-password"
                        onClick={() => setShowPassword(!showPassword)}
                        title={showPassword ? "Hide password" : "Show password"}
                        aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                        {showPassword ? (
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/>
                                <line x1="1" y1="1" x2="23" y2="23"/>
                            </svg>
                        ) : (
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                                <circle cx="12" cy="12" r="3"/>
                            </svg>
                        )}
                    </button>
                </div>

                <select
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                    className="register-select"
                    aria-label="Role"
                >
                    <option value="user">User</option>
                    <option value="admin">Admin</option>
                </select>

                <button type="submit" className="register-button">
                    Register
                </button>
            </form>

            {message && (
                <p className={`register-message ${message.toLowerCase().includes("success") ? "success" : "error"}`}>
                    {message}
                </p>
            )}
        </div>
    );
}
