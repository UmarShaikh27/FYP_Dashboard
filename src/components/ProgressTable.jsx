// components/ProgressTable.jsx
import * as XLSX from "xlsx";

export default function ProgressTable({ patient, sessions, loading, patients, onSelectPatient }) {
  const exportXLSX = () => {
    if (!sessions.length) return;
    const rows = sessions.map((s) => ({
      Date:              s.createdAt?.toDate?.().toLocaleDateString() ?? "—",
      Exercise:          s.exerciseName,
      "Duration (min)":  s.durationMinutes,
      Reps:              s.repsCompleted,
      Notes:             s.notes || "",
    }));
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Sessions");
    XLSX.writeFile(wb, `${patient?.name ?? "patient"}_sessions.xlsx`);
  };

  return (
    <div className="records-view">
      <div className="records-header">
        <div>
          <h1>Exercise Records</h1>
          {patient && <p className="subtitle">Showing records for <strong>{patient.name}</strong></p>}
        </div>
        <div className="records-actions">
          {/* Patient switcher */}
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
          <button className="btn-export" onClick={exportXLSX} disabled={!sessions.length}>
            ⬇ Export XLSX
          </button>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">Loading records…</div>
      ) : sessions.length === 0 ? (
        <div className="empty-state">No sessions recorded yet for this patient.</div>
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
    </div>
  );
}
