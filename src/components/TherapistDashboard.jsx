// components/TherapistDashboard.jsx
import { useEffect, useState } from "react";
import { logoutUser } from "../firebase/auth";
import { getAllPatients, getPatientSessions, saveSession } from "../firebase/db";
import ExerciseSession from "./ExerciseSession";
import ProgressTable from "./ProgressTable";

export default function TherapistDashboard({ user, onLogout }) {
  const [patients, setPatients]           = useState([]);
  const [selectedPatient, setSelectedPatient] = useState(null);
  const [sessions, setSessions]           = useState([]);
  const [view, setView]                   = useState("home"); // home | session | records
  const [loading, setLoading]             = useState(false);

  useEffect(() => {
    getAllPatients().then(setPatients);
  }, []);

  const loadSessions = async (patient) => {
    setSelectedPatient(patient);
    setLoading(true);
    const data = await getPatientSessions(patient.id);
    setSessions(data);
    setLoading(false);
    setView("records");
  };

  const handleLogout = async () => {
    await logoutUser();
    onLogout();
  };

  return (
    <div className="dashboard">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <span className="logo-icon">✦</span>
          <span>PhysioSync</span>
        </div>
        <nav className="sidebar-nav">
          <button className={view === "home"    ? "active" : ""} onClick={() => setView("home")}>🏠 Home</button>
          <button className={view === "session" ? "active" : ""} onClick={() => setView("session")}>🎮 New Session</button>
          <button className={view === "records" ? "active" : ""} onClick={() => setView("records")}>📊 Records</button>
        </nav>
        <div className="sidebar-footer">
          <p className="sidebar-user">Dr. {user.name}</p>
          <button className="btn-logout" onClick={handleLogout}>Sign Out</button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        {view === "home" && (
          <div className="home-view">
            <h1>Welcome back, <span className="accent">Dr. {user.name}</span></h1>
            <p className="subtitle">Select a patient to begin a session or review records.</p>
            <div className="patient-grid">
              {patients.map((p) => (
                <div key={p.id} className="patient-card">
                  <div className="patient-avatar">{p.name?.charAt(0).toUpperCase()}</div>
                  <h3>{p.name}</h3>
                  <p>{p.email}</p>
                  <div className="card-actions">
                    <button className="btn-primary" onClick={() => { setSelectedPatient(p); setView("session"); }}>
                      Start Session
                    </button>
                    <button className="btn-secondary" onClick={() => loadSessions(p)}>
                      View Records
                    </button>
                  </div>
                </div>
              ))}
              {patients.length === 0 && (
                <p className="empty-state">No patients found. Add patients via Firebase Console.</p>
              )}
            </div>
          </div>
        )}

        {view === "session" && (
          <ExerciseSession
            patient={selectedPatient}
            patients={patients}
            therapistId={user.uid}
            onSaved={() => setView("home")}
          />
        )}

        {view === "records" && (
          <ProgressTable
            patient={selectedPatient}
            sessions={sessions}
            loading={loading}
            patients={patients}
            onSelectPatient={loadSessions}
          />
        )}
      </main>
    </div>
  );
}
