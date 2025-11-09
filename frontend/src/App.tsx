import { useState } from "react";
import { Routes, Route } from "react-router-dom";
import axios from "axios";
import { BrowserRouter, Routes, Route, Link, Navigate } from "react-router-dom";
import Register from "./pages/register/Register";
import MainPage from "./pages/mainpage/MainPage";


function App() {
  // State to hold the response message from the backend
  const [response, setResponse] = useState<string>("");
  const API_BASE = import.meta.env.VITE_API_BASE.replace(/\/$/, "");; // Get the backend API base URL from environment variables, during development it is usually http://

  // Function to handle button click and communicate with backend
  const handleClick = async () => {
    try {
      // Send GET request to backend /hello endpoint
      const res = await axios.get(`${API_BASE}/hello`);
      // setResponse updates the state variable 'response' with the message from the backend
      setResponse(res.data.message);
    } catch (error) {
      console.error("Error connecting to backend:", error);
      setResponse("Error connecting to backend");
    }
  };

  return (
     // Wrapped content in BrowserRouter + added routes for Register and MainPage
      <BrowserRouter>
          <div style={{textAlign: "center", marginTop: "50px"}}>
              <h1>Frontend → Backend Test</h1>
              {/* Button to trigger backend communication, after pressed it will call the handleClick function */}
              <button onClick={handleClick}>Talk to Backend</button>
              <p>{response}</p>
              {/*Added navigation links for easy switching*/}
              <nav style={{marginBottom: "20px"}}>
                  <Link to="/mainpage" style={{marginRight: "10px"}}>MainPage</Link>
                  <Link to="/register">Register</Link>
              </nav>

              {/* Added routes so can see both pages */}
              <Routes>
                  {/* If the URL is '/mainpage', show the MainPage component. */}
                  <Route path="/mainpage" element={<MainPage />} />
                  {/* If the URL is '/register', show the Register component. */}
                  <Route path="/register" element={<Register />} />
                  {/* If the user goes to any other URL, it will automatically redirect them to '/mainpage' */}
                  <Route path="*" element={<Navigate to="/mainpage" replace />} />
              </Routes>

          </div>
      </BrowserRouter>

);
}

export default App;
