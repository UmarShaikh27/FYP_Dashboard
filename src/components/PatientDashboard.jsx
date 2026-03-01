// components/PatientDashboard.jsx
import { useEffect, useState } from "react";
import { logoutUser } from "../firebase/auth";
import { getPatientSessions, getPatientAnalyses } from "../firebase/db";

function scoreColor(s) {
  return s >= 80 ? "#00e5c3" : s >= 50 ? "#0090ff" : "#ff4b6e";
}

export default function PatientDashboard({ user, onLogout }) {
  const [sessions, setSessions]   = useState([]);
  const [analyses, setAnalyses]   = useState([]);
  const [loading, setLoading]     = useState(true);
  const [expandedId, setExpanded] = useState(null);
  const [activeTab, setActiveTab] = useState("analyses");

  useEffect(() => {
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

  const totalAnalyses = analyses.length;
  const avgScore = totalAnalyses
    ? Math.round(analyses.reduce((s, a) => s + (a.score || 0), 0) / totalAnalyses)
    : 0;
  const bestScore = totalAnalyses
    ? Math.max(...analyses.map((a) => a.score || 0))
    : 0;

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

        <div className="stats-grid">
          <div className="stat-card">
            <span className="stat-number" style={{ color: scoreColor(avgScore) }}>{avgScore}</span>
            <span className="stat-label">Average Score</span>
          </div>
          <div className="stat-card">
            <span className="stat-number" style={{ color: scoreColor(bestScore) }}>{bestScore}</span>
            <span className="stat-label">Best Score</span>
          </div>
          <div className="stat-card">
            <span className="stat-number">{totalAnalyses}</span>
            <span className="stat-label">Total Sessions</span>
          </div>
        </div>

        <div className="record-tabs" style={{ marginTop: "2rem" }}>
          <button className={activeTab === "analyses" ? "active" : ""} onClick={() => setActiveTab("analyses")}>
            🔬 My Analysis Results
          </button>
          <button className={activeTab === "sessions" ? "active" : ""} onClick={() => setActiveTab("sessions")}>
            📋 Manual Sessions
          </button>
        </div>

        {loading ? (
          <div className="loading-state">Loading…</div>
        ) : (
          <>
            {activeTab === "analyses" && (
              analyses.length === 0 ? (
                <div className="empty-state">No analysis results yet. Your therapist will run an assessment during your next visit.</div>
              ) : (
                <div className="analysis-list">
                  {analyses.map((a) => (
                    <div key={a.id} className="analysis-card">
                      <div className="analysis-card-header" onClick={() => setExpanded(expandedId === a.id ? null : a.id)}>
                        <div className="analysis-left">
                          <span className="exercise-badge">{a.exerciseName}</span>
                          <span className="analysis-date">{a.createdAt?.toDate?.().toLocaleDateString() ?? "—"}</span>
                        </div>
                        <div className="analysis-center">
                          <span className="analysis-score" style={{ color: scoreColor(a.score) }}>{a.score}/100</span>
                          <span className="analysis-score-label">Score</span>
                        </div>
                        <div className="analysis-grades">
                          <span className="mini-grade">ROM: <b>{a.avg_rom_grade ? Math.round(a.avg_rom_grade) : "—"}/10</b></span>
                          <span className="mini-grade">Shape: <b>{a.shape_grade ?? "—"}/10</b></span>
                        </div>
                        <button className="expand-btn">{expandedId === a.id ? "▲" : "▼"}</button>
                      </div>
                      {expandedId === a.id && (
                        <div className="analysis-card-body">
                          <div className="analysis-details-grid">
                            <div>
                              <h4>Therapist Feedback</h4>
                              <pre className="report-pre small">{a.report_text}</pre>
                            </div>
                            {a.plot_image_b64 && (
                              <div>
                                <h4>Your Motion vs Expert</h4>
                                <img
                                  src={`data:image/png;base64,${a.plot_image_b64}`}
                                  alt="Trajectory comparison"
                                  className="result-plot small-plot"
                                />
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )
            )}
            {activeTab === "sessions" && (
              sessions.length === 0 ? (
                <div className="empty-state">No manual sessions recorded yet.</div>
              ) : (
                <div className="table-wrapper">
                  <table className="records-table">
                    <thead>
                      <tr><th>Date</th><th>Exercise</th><th>Duration</th><th>Reps</th><th>Notes</th></tr>
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
              )
            )}
          </>
        )}
      </main>
    </div>
  );
}
