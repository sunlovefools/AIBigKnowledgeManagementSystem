import { useState } from "react";
import axios from "axios";
import Register from "./pages/register/Register";

function App() {
  // State to hold the response message from the backend
  const [response, setResponse] = useState<string>("");
  const API_BASE = import.meta.env.VITE_API_BASE; // Get the backend API base URL from environment variables, during development it is usually http://

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
    <div style={{ textAlign: "center", marginTop: "50px" }}>
      <h1>Frontend → Backend Test</h1>
      {/* Button to trigger backend communication, after pressed it will call the handleClick function */}
      <button onClick={handleClick}>Talk to Backend</button>
      <p>{response}</p>
      {/* Registration component at the bottom */}
      <Register />
    </div>
  );
}

export default App;
