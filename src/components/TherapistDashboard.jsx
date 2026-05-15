// components/TherapistDashboard.jsx
import { useEffect, useState } from "react";
import { logoutUser } from "../firebase/auth";
import { getAllPatients, getPatientSessions, getPatientAnalyses } from "../firebase/db";
import ExerciseSession from "./ExerciseSession";
import ProgressTable from "./ProgressTable";
import PipelineRunner from "./PipelineRunner";
import ScoringMethodology from "./ScoringMethodology";

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

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("tdView") === "scoring") {
      setView("scoring");
    }
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

  // Remove deleted records from local state immediately — no reload needed
  const handleAnalysisDeleted = (id) => {
    setAnalyses((prev) => prev.filter((a) => a.id !== id));
  };

  const handleSessionDeleted = (id) => {
    setSessions((prev) => prev.filter((s) => s.id !== id));
  };

  const handleLogout = async () => {
    await logoutUser();
    onLogout();
  };

  const openScoringMethodologyInNewTab = () => {
    const url = new URL(window.location.href);
    url.searchParams.set("tdView", "scoring");
    window.open(url.toString(), "_blank", "noopener,noreferrer");
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
          <button className={view === "pipeline" ? "active" : ""} onClick={() => setView("pipeline")}>🔬 Therapy Session</button>
          <button className={view === "records"  ? "active" : ""} onClick={() => setView("records")}>📊 Records</button>
          <button className={view === "scoring"  ? "active" : ""} onClick={() => setView("scoring")}>🧠 Scoring Methodology</button>
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
                      Launch Session
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

        {view === "records" && (
          <div className="records-view">
            {/* Patient selector when landing on Records tab directly */}
            {!selectedPatient ? (
              <div className="home-view">
                <h2>Select a patient to view records</h2>
                <div className="patient-grid">
                  {patients.map((p) => (
                    <div key={p.id} className="patient-card" onClick={() => loadRecords(p)} style={{ cursor: 'pointer' }}>
                      <div className="patient-avatar">{p.name?.charAt(0).toUpperCase()}</div>
                      <h3>{p.name}</h3>
                      <p>{p.email}</p>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                  <button className="btn-secondary" onClick={() => { setSelectedPatient(null); setAnalyses([]); setSessions([]); }}>
                    ← Back to patients
                  </button>
                  <div style={{ color: '#cbd5e1' }}>
                    Viewing records for <strong>{selectedPatient.name}</strong>
                  </div>
                  <select
                    value={selectedPatient.id}
                    onChange={(e) => loadRecords(patients.find(p => p.id === e.target.value))}
                    style={{ marginLeft: 'auto' }}
                  >
                    {patients.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </div>
                <ProgressTable
                  analysisResults={analyses}
                  sessions={sessions}
                  patient={selectedPatient}
                  loading={loading}
                  onAnalysisDeleted={handleAnalysisDeleted}
                  onSessionDeleted={handleSessionDeleted}
                  onOpenScoreMethodology={openScoringMethodologyInNewTab}
                />
              </>
            )}
          </div>
        )}

        {view === "scoring" && <ScoringMethodology />}
      </main>
    </div>
  );
}
