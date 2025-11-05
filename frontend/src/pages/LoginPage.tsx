import { useState } from "react"; /*allows to store user input*/
import "./LoginPage.css";

export default function LoginPage() {
    /*setting up state variables */
  const [username, setUsername] = useState("");/*store user username box input*/
  const [password, setPassword] = useState("");/*store password box input */
  const [role, setRole] = useState("user");/*store selected role, default set to user*/
  const [message, setMessage] = useState("");/*store info message displayed */

  const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?":{}|<>]).{8,}$/;/*regex to enforce password requirements*/

  async function handleSubmit(e: React.FormEvent) {/*ran when user clicks login*/
    e.preventDefault();/*STOPS FROM DEFAULT RELOADING PAGE*/

    if (!passwordRegex.test(password)) {/*check passwordf valid*/
      setMessage(
        "INVALID PASSWORD: must have minimum 8 chars, 1 uppercase, 1 lowercase, 1 number, and 1 special character"
      );
      return;/*end function if invalid*/
    }

    /*SENDING POST REQ TO BACKEND */
    try {
      const res = await fetch("http://localhost:8000/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },/*send username,password and role as JSON to fast API */
        body: JSON.stringify({ username, password, role }),
      });
    /*HANDLING BACKEND RESPONSE */
      const data = await res.json();
      if (res.ok) {/*HTTP CODE = 200-299 */
        setMessage("Login successful");
        if (data.token) localStorage.setItem("token", data.token);/*store JWT token in browser local storage to stay logged in*/
      } else {
        setMessage("Login failed");
      }
    } catch {
      setMessage("Error whens ending POST");
    }
  }

  /*FORMATTING THE PAGE */
  return (
    <div className="login-page">
        <div className="login-container">
        <h2>Login</h2>

        {/*LOGIN FORM: runs handleSubmit when login clicked*/}
        <form onSubmit={handleSubmit}> 
            {/*username INPUT*/}
            <input
            type="username"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required/*cannot be blank*/
            style={{width: "100%", marginBottom:10}}
            />
            {/*password INPUT*/}
            <input
            type="password"/*hides what user types*/
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={{width: "100%", marginBottom:10}}
            />
            {/*role select DROPDOWN MENU*/}
            <h5>Select your role:</h5>
            <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            style={{width: "100%", marginBottom:10}}
            >
            <option value="user">User</option>
            <option value="admin">Admin</option>
            </select>{/*SUBMIT BUTTON*/}
            <button type="submit" style={{width:"100%", padding:8}}>
            Login
            </button>
        </form>
        <p style={{marginTop: 10}}>{message}</p>{/*displays info message e.g. login success*/}
        <p>
            Don’t have an account yet? <a href="/register">Register</a>
        </p>
        </div>
    </div>
  );
}
