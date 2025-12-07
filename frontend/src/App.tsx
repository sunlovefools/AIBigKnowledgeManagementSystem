
import { Routes, Route, Navigate } from "react-router-dom";
import Register from "./pages/register/Register";
import MainPage from "./pages/mainpage/MainPage";


function App() {
  return (
    <Routes>
      <Route path="/mainpage" element={<MainPage />} />
      <Route path="/register" element={<Register />} />
      <Route path="*" element={<Navigate to="/mainpage" replace />} />
    </Routes>
  );
}

export default App;
