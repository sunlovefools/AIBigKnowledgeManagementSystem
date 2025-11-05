import { useState } from "react";
import axios from "axios";

function App() {
  const [response, setResponse] = useState<string>("");

  const handleClick = async () => {
    try {
      const res = await axios.get("http://127.0.0.1:8000/hello");
      setResponse(res.data.message);
    } catch (error) {
      console.error("Error connecting to backend:", error);
      setResponse("Error connecting to backend");
    }
  };

  return (
    <div style={{ textAlign: "center", marginTop: "50px" }}>
      <h1>Frontend → Backend Test</h1>
      <button onClick={handleClick}>Talk to Backend</button>
      <p>{response}</p>
    </div>
  );
}

export default App;
