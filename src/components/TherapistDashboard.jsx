// components/TherapistDashboard.jsx
import { useEffect, useState } from "react";
import { logoutUser } from "../firebase/auth";
import { getAllPatients, getPatientSessions, getPatientAnalyses } from "../firebase/db";
import ExerciseSession from "./ExerciseSession";
import ProgressTable from "./ProgressTable";
import PipelineRunner from "./PipelineRunner";

export default function TherapistDashboard({ user, onLogout }) {
  const [patients, setPatients]               = useState([]);
  const [selectedPatient, setSelectedPatient] = useState(null);
  const [sessions, setSessions]               = useState([]);
  const [analyses, setAnalyses]               = useState([]);
  const [view, setView]                       = useState("home");
  const [loading, setLoading]                 = useState(false);

  useEffect(() => {
    getAllPatients().then(setPatients);
  }, []);

  const loadRecords = async (patient) => {
    setSelectedPatient(patient);
    setLoading(true);
    const [sess, anal] = await Promise.all([
      getPatientSessions(patient.id),
      getPatientAnalyses(patient.id),
    ]);
    setSessions(sess);
    setAnalyses(anal);
    setLoading(false);
    setView("records");
  };

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
          <button className={view === "home"     ? "active" : ""} onClick={() => setView("home")}>🏠 Home</button>
          <button className={view === "pipeline" ? "active" : ""} onClick={() => setView("pipeline")}>🔬 Run Analysis</button>
          <button className={view === "session"  ? "active" : ""} onClick={() => setView("session")}>🎮 Manual Session</button>
          <button className={view === "records"  ? "active" : ""} onClick={() => setView("records")}>📊 Records</button>
        </nav>
        <div className="sidebar-footer">
          <p className="sidebar-user">Dr. {user.name}</p>
          <button className="btn-logout" onClick={handleLogout}>Sign Out</button>
        </div>
      </aside>

      <main className="main-content">
        {view === "home" && (
          <div className="home-view">
            <h1>Welcome back, <span className="accent">Dr. {user.name}</span></h1>
            <p className="subtitle">Select a patient to run an analysis or review records.</p>
            <div className="patient-grid">
              {patients.map((p) => (
                <div key={p.id} className="patient-card">
                  <div className="patient-avatar">{p.name?.charAt(0).toUpperCase()}</div>
                  <h3>{p.name}</h3>
                  <p>{p.email}</p>
                  <div className="card-actions">
                    <button className="btn-primary" onClick={() => { setSelectedPatient(p); setView("pipeline"); }}>
                      Run Analysis
                    </button>
                    <button className="btn-secondary" onClick={() => loadRecords(p)}>
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

        {view === "pipeline" && (
          <PipelineRunner
            patient={selectedPatient}
            patients={patients}
            therapistId={user.uid}
            onSaved={() => setView("home")}
          />
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
            analyses={analyses}
            loading={loading}
            patients={patients}
            onSelectPatient={loadRecords}
          />
        )}
      </main>
    </div>
  );
}
