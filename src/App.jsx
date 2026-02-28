// App.jsx
import { useEffect, useState } from "react";
import { onAuthChange } from "./firebase/auth";
import Login from "./components/Login";
import TherapistDashboard from "./components/TherapistDashboard";
import PatientDashboard from "./components/PatientDashboard";
import "./index.css";



export default function App() {
  const [user, setUser] = useState(undefined); // undefined = loading

  useEffect(() => {
    const unsubscribe = onAuthChange((u) => setUser(u));
    return () => unsubscribe();
  }, []);

  if (user === undefined) {
    return (
      <div className="splash">
        <div className="spinner" />
      </div>
    );
  }

  if (!user) return <Login onLogin={setUser} />;

  if (user.role === "therapist") return <TherapistDashboard user={user} onLogout={() => setUser(null)} />;
  if (user.role === "patient")   return <PatientDashboard  user={user} onLogout={() => setUser(null)} />;

  return <p style={{ color: "red", padding: 40 }}>Unknown role. Contact your administrator.</p>;
}
