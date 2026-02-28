// components/PatientDashboard.jsx
import { useEffect, useState } from "react";
import { logoutUser } from "../firebase/auth";
import { getPatientSessions } from "../firebase/db";

export default function PatientDashboard({ user, onLogout }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading]   = useState(true);

  useEffect(() => {
    getPatientSessions(user.uid).then((data) => {
      setSessions(data);
      setLoading(false);
    });
  }, [user.uid]);

  const handleLogout = async () => {
    await logoutUser();
    onLogout();
  };

  // Simple stats
  const totalSessions  = sessions.length;
  const totalMinutes   = sessions.reduce((sum, s) => sum + (s.durationMinutes || 0), 0);
  const totalReps      = sessions.reduce((sum, s) => sum + (s.repsCompleted   || 0), 0);

  return (
    <div className="dashboard">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <span className="logo-icon">✦</span>
          <span>PhysioSync</span>
        </div>
        <div className="sidebar-footer">
          <p className="sidebar-user">{user.name}</p>
          <button className="btn-logout" onClick={handleLogout}>Sign Out</button>
        </div>
      </aside>

      <main className="main-content">
        <h1>My Progress</h1>
        <p className="subtitle">Track your rehabilitation journey.</p>

        {/* Stats */}
        <div className="stats-grid">
          <div className="stat-card">
            <span className="stat-number">{totalSessions}</span>
            <span className="stat-label">Total Sessions</span>
          </div>
          <div className="stat-card">
            <span className="stat-number">{totalMinutes}</span>
            <span className="stat-label">Minutes Exercised</span>
          </div>
          <div className="stat-card">
            <span className="stat-number">{totalReps}</span>
            <span className="stat-label">Total Reps</span>
          </div>
        </div>

        {/* History */}
        <h2 style={{ marginTop: "2.5rem", marginBottom: "1rem" }}>Session History</h2>
        {loading ? (
          <div className="loading-state">Loading…</div>
        ) : sessions.length === 0 ? (
          <div className="empty-state">No sessions yet. Your therapist will add them after each visit.</div>
        ) : (
          <div className="table-wrapper">
            <table className="records-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Exercise</th>
                  <th>Duration</th>
                  <th>Reps</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td>{s.createdAt?.toDate?.().toLocaleDateString() ?? "—"}</td>
                    <td><span className="exercise-badge">{s.exerciseName}</span></td>
                    <td>{s.durationMinutes} min</td>
                    <td>{s.repsCompleted}</td>
                    <td className="notes-cell">{s.notes || <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
