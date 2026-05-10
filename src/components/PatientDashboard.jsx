// components/PatientDashboard.jsx
import { useEffect, useState } from "react";
import { logoutUser } from "../firebase/auth";
import { getPatientSessions, getPatientAnalyses } from "../firebase/db";
import ProgressTable from "./ProgressTable";

export default function PatientDashboard({ user, onLogout }) {
  const [sessions,  setSessions]  = useState([]);
  const [analyses,  setAnalyses]  = useState([]);
  const [loading,   setLoading]   = useState(true);

  // Build a minimal "patient" object that matches the shape ProgressTable expects
  const patient = { id: user.uid, name: user.name, email: user.email };

  useEffect(() => {
    setLoading(true);
    Promise.all([
      getPatientSessions(user.uid),
      getPatientAnalyses(user.uid),
    ]).then(([sess, anal]) => {
      setSessions(sess);
      setAnalyses(anal);
      setLoading(false);
    });
  }, [user.uid]);

  const handleLogout = async () => {
    await logoutUser();
    onLogout();
  };

  return (
    <div className="dashboard">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <span className="logo-icon">✦</span>
          <span>PhysioSync</span>
        </div>
        <nav className="sidebar-nav">
          <button className="active">📊 My Records</button>
        </nav>
        <div className="sidebar-footer">
          <p className="sidebar-user">{user.name}</p>
          <button className="btn-logout" onClick={handleLogout}>Sign Out</button>
        </div>
      </aside>

      <main className="main-content">
        <ProgressTable
          patient={patient}
          sessions={sessions}
          analyses={analyses}
          loading={loading}
        />
      </main>
    </div>
  );
}
