
import { Routes, Route, Link, Navigate } from "react-router-dom";
import Register from "./pages/register/Register";
import MainPage from "./pages/mainpage/MainPage";


function App() {
  return (
    <div style={{ padding: "20px", fontFamily: "Arial, sans-serif" }}>
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
  );
}
export default App;
