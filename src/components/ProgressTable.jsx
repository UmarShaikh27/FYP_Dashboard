// components/ProgressTable.jsx
import * as XLSX from "xlsx";
import { useState } from "react";
import { deleteAnalysisResult, deleteSession } from "../firebase/db";

function scoreColor(score) {
  if (score >= 80) return "#00e5c3";
  if (score >= 50) return "#0090ff";
  return "#ff4b6e";
}

function DeleteConfirm({ onConfirm, onCancel, label }) {
  return (
    <div className="delete-confirm">
      <span>Delete this {label}?</span>
      <button className="btn-delete-confirm" onClick={onConfirm}>Delete</button>
      <button className="btn-delete-cancel" onClick={onCancel}>Cancel</button>
    </div>
  );
}

export default function ProgressTable({
  patient,
  sessions,
  analyses = [],
  loading,
  patients,
  onSelectPatient,
  onAnalysisDeleted,
  onSessionDeleted,
}) {
  const [activeTab, setActiveTab]     = useState("analyses");
  const [expandedId, setExpandedId]   = useState(null);
  const [deletingId, setDeletingId]   = useState(null);
  const [deleteType, setDeleteType]   = useState(null);
  const [deleteError, setDeleteError] = useState("");

  const exportXLSX = () => {
    const rows = analyses.map((a) => ({
      Date:             a.createdAt?.toDate?.().toLocaleDateString() ?? "—",
      Exercise:         a.exerciseName,
      "Recording File": a.recordingFile ?? "—",
      Score:            a.score,
      "ROM Grade":      a.avg_rom_grade ? Math.round(a.avg_rom_grade) : "—",
      "Shape Grade":    a.shape_grade ?? "—",
      "Global RMSE":    a.global_rmse,
      "ROM Ratio %":    a.rom_ratio ? (a.rom_ratio * 100).toFixed(1) : "—",
    }));
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Analysis Results");
    XLSX.writeFile(wb, `${patient?.name ?? "patient"}_analyses.xlsx`);
  };

  const handleDeleteAnalysis = async (id) => {
    setDeleteError("");
    try {
      await deleteAnalysisResult(id);
      onAnalysisDeleted?.(id);
      setDeletingId(null);
      if (expandedId === id) setExpandedId(null);
    } catch (e) {
      setDeleteError("Failed to delete: " + e.message);
    }
  };

  const handleDeleteSession = async (id) => {
    setDeleteError("");
    try {
      await deleteSession(id);
      onSessionDeleted?.(id);
      setDeletingId(null);
    } catch (e) {
      setDeleteError("Failed to delete: " + e.message);
    }
  };

  const downloadExcelFile = (analysis) => {
    if (!analysis.excel_file_b64) {
      alert("Excel file not available for this record.");
      return;
    }
    try {
      // Convert base64 to blob
      const binaryString = atob(analysis.excel_file_b64);
      const bytes = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }
      const blob = new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
      
      // Trigger download
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = analysis.patientFile;  // Use the original timestamped filename
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (e) {
      alert("Failed to download Excel file: " + e.message);
    }
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
            <option value="">Switch Patient</option>
            {patients?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="btn-export" onClick={exportXLSX} disabled={!analyses.length}>
            Export XLSX
          </button>
        </div>
      </div>

      {deleteError && (
        <div className="pipeline-error" style={{ marginBottom: 16 }}>{deleteError}</div>
      )}

      <div className="record-tabs">
        <button
          className={activeTab === "analyses" ? "active" : ""}
          onClick={() => setActiveTab("analyses")}
        >
          Analysis Results {analyses.length > 0 && <span className="tab-badge">{analyses.length}</span>}
        </button>
        <button
          className={activeTab === "sessions" ? "active" : ""}
          onClick={() => setActiveTab("sessions")}
        >
          Manual Sessions {sessions.length > 0 && <span className="tab-badge">{sessions.length}</span>}
        </button>
      </div>

      {loading ? (
        <div className="loading-state">Loading records…</div>
      ) : (
        <>
          {/* Analysis Results Tab */}
          {activeTab === "analyses" && (
            analyses.length === 0 ? (
              <div className="empty-state">
                No analysis results yet. Run an analysis from the Run Analysis tab.
              </div>
            ) : (
              <div className="analysis-list">
                {analyses.map((a) => (
                  <div key={a.id} className="analysis-card">

                    <div className="analysis-card-header">
                      <div
                        className="analysis-header-clickable"
                        onClick={() => setExpandedId(expandedId === a.id ? null : a.id)}
                      >
                        <div className="analysis-left">
                          <span className="exercise-badge">{a.exerciseName}</span>
                          <span className="analysis-date">
                            {a.createdAt?.toDate?.().toLocaleDateString() ?? "—"}
                          </span>
                          {a.recordingFile && (
                            <span className="recording-filename" title={a.recordingFile}>
                              {a.recordingFile}
                            </span>
                          )}
                        </div>
                        <div className="analysis-center">
                          <span className="analysis-score" style={{ color: scoreColor(a.score) }}>
                            {a.score}/100
                          </span>
                          <span className="analysis-score-label">Score</span>
                        </div>
                        <div className="analysis-grades">
                          <span className="mini-grade">
                            ROM: <b>{a.avg_rom_grade ? Math.round(a.avg_rom_grade) : "—"}/10</b>
                          </span>
                          <span className="mini-grade">
                            Shape: <b>{a.shape_grade ?? "—"}/10</b>
                          </span>
                          {a.sparc_grades && (
                            <span className="mini-grade">
                              Smooth: <b>{a.sparc_grades.total}/10</b>
                            </span>
                          )}
                        </div>
                        <button className="expand-btn">
                          {expandedId === a.id ? "▲" : "▼"}
                        </button>
                      </div>

                      <div className="card-delete-area" onClick={(e) => e.stopPropagation()}>
                        {a.excel_file_b64 && (
                          <button 
                            className="btn-download"
                            onClick={() => downloadExcelFile(a)}
                            title="Download motion capture data"
                          >
                            📥 Excel
                          </button>
                        )}
                        {deletingId === a.id && deleteType === "analysis" ? (
                          <DeleteConfirm
                            label="analysis"
                            onConfirm={() => handleDeleteAnalysis(a.id)}
                            onCancel={() => setDeletingId(null)}
                          />
                        ) : (
                          <button
                            className="btn-delete"
                            onClick={() => {
                              setDeletingId(a.id);
                              setDeleteType("analysis");
                              setDeleteError("");
                            }}
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </div>

                    {expandedId === a.id && (
                      <div className="analysis-card-body">
                        {a.recordingFile && (
                          <div className="recording-info-bar">
                            <span className="recording-info-label">Recording file</span>
                            <code className="recording-info-path">{a.recordingFile}</code>
                          </div>
                        )}
                        <div className="analysis-details-grid">
                          <div>
                            <h4>Therapist Report</h4>
                            <pre className="report-pre small">{a.report_text}</pre>
                          </div>
                          <div>
                            <h4>Analysis Plot</h4>
                            {a.plot_image_b64 ? (
                              <img
                                src={`data:image/png;base64,${a.plot_image_b64}`}
                                alt="DTW plot"
                                className="result-plot small-plot"
                              />
                            ) : (
                              <p className="muted">No plot saved.</p>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )
          )}

          {/* Manual Sessions Tab */}
          {activeTab === "sessions" && (
            sessions.length === 0 ? (
              <div className="empty-state">No manual sessions recorded yet.</div>
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
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((s) => (
                      <tr key={s.id}>
                        <td>{s.createdAt?.toDate?.().toLocaleDateString() ?? "—"}</td>
                        <td><span className="exercise-badge">{s.exerciseName}</span></td>
                        <td>{s.durationMinutes} min</td>
                        <td>{s.repsCompleted}</td>
                        <td className="notes-cell">
                          {s.notes || <span className="muted">—</span>}
                        </td>
                        <td>
                          {deletingId === s.id && deleteType === "session" ? (
                            <DeleteConfirm
                              label="session"
                              onConfirm={() => handleDeleteSession(s.id)}
                              onCancel={() => setDeletingId(null)}
                            />
                          ) : (
                            <button
                              className="btn-delete"
                              onClick={() => {
                                setDeletingId(s.id);
                                setDeleteType("session");
                                setDeleteError("");
                              }}
                            >
                              Delete
                            </button>
                          )}
                        </td>
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
