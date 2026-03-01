// components/ProgressTable.jsx
import * as XLSX from "xlsx";
import { useState } from "react";

function scoreColor(score) {
  if (score >= 80) return "#00e5c3";
  if (score >= 50) return "#0090ff";
  return "#ff4b6e";
}

export default function ProgressTable({ patient, sessions, analyses = [], loading, patients, onSelectPatient }) {
  const [activeTab, setActiveTab] = useState("analyses"); // "analyses" | "sessions"
  const [expandedId, setExpandedId] = useState(null);

  const exportXLSX = () => {
    const rows = analyses.map((a) => ({
      Date:           a.createdAt?.toDate?.().toLocaleDateString() ?? "—",
      Exercise:       a.exerciseName,
      Score:          a.score,
      "ROM Grade":    a.avg_rom_grade ? Math.round(a.avg_rom_grade) : "—",
      "Shape Grade":  a.shape_grade ?? "—",
      "Global RMSE":  a.global_rmse,
      "ROM Ratio %":  a.rom_ratio ? (a.rom_ratio * 100).toFixed(1) : "—",
    }));
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Analysis Results");
    XLSX.writeFile(wb, `${patient?.name ?? "patient"}_analyses.xlsx`);
  };

  return (
    <div className="records-view">
      <div className="records-header">
        <div>
          <h1>Patient Records</h1>
          {patient && <p className="subtitle">Showing records for <strong>{patient.name}</strong></p>}
        </div>
        <div className="records-actions">
          <select
            value={patient?.id || ""}
            onChange={(e) => {
              const p = patients?.find((p) => p.id === e.target.value);
              if (p) onSelectPatient(p);
            }}
          >
            <option value="">— Switch Patient —</option>
            {patients?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="btn-export" onClick={exportXLSX} disabled={!analyses.length}>
            ⬇ Export XLSX
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="record-tabs">
        <button className={activeTab === "analyses" ? "active" : ""} onClick={() => setActiveTab("analyses")}>
          🔬 Analysis Results {analyses.length > 0 && <span className="tab-badge">{analyses.length}</span>}
        </button>
        <button className={activeTab === "sessions" ? "active" : ""} onClick={() => setActiveTab("sessions")}>
          📋 Manual Sessions {sessions.length > 0 && <span className="tab-badge">{sessions.length}</span>}
        </button>
      </div>

      {loading ? (
        <div className="loading-state">Loading records…</div>
      ) : (
        <>
          {/* ── Analysis Results Tab ── */}
          {activeTab === "analyses" && (
            analyses.length === 0 ? (
              <div className="empty-state">No analysis results yet. Run an analysis from the "🔬 Run Analysis" tab.</div>
            ) : (
              <div className="analysis-list">
                {analyses.map((a) => (
                  <div key={a.id} className="analysis-card">
                    <div className="analysis-card-header" onClick={() => setExpandedId(expandedId === a.id ? null : a.id)}>
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
                            <h4>Therapist Report</h4>
                            <pre className="report-pre small">{a.report_text}</pre>
                          </div>
                          <div>
                            <h4>3D Trajectory</h4>
                            {a.plot_image_b64 ? (
                              <img
                                src={`data:image/png;base64,${a.plot_image_b64}`}
                                alt="DTW plot"
                                className="result-plot small-plot"
                              />
                            ) : <p className="muted">No plot saved.</p>}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )
          )}

          {/* ── Manual Sessions Tab ── */}
          {activeTab === "sessions" && (
            sessions.length === 0 ? (
              <div className="empty-state">No manual sessions recorded yet.</div>
            ) : (
              <div className="table-wrapper">
                <table className="records-table">
                  <thead>
                    <tr>
                      <th>Date</th><th>Exercise</th><th>Duration</th><th>Reps</th><th>Notes</th>
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
            )
          )}
        </>
      )}
    </div>
  );
}
