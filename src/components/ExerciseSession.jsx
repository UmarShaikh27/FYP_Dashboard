// components/ExerciseSession.jsx
import { useState } from "react";
import { saveSession } from "../firebase/db";

const EXERCISES = [
  "Shoulder Rotation",
  "Knee Flexion",
  "Hip Abduction",
  "Ankle Dorsiflexion",
  "Wrist Extension",
  "Balance Training",
];

export default function ExerciseSession({ patient, patients, therapistId, onSaved }) {
  const [selectedPatient, setSelectedPatient] = useState(patient || null);
  const [exercise, setExercise]   = useState(EXERCISES[0]);
  const [duration, setDuration]   = useState(15);
  const [reps, setReps]           = useState(10);
  const [notes, setNotes]         = useState("");
  const [saving, setSaving]       = useState(false);
  const [launched, setLaunched]   = useState(false);
  const [saved, setSaved]         = useState(false);

  // Launches the Unity game via a custom URL scheme
  // You must register "physio://" in your Unity build's manifest / Info.plist
  const launchUnity = () => {
    window.location.href = `physio://start?exercise=${encodeURIComponent(exercise)}&reps=${reps}&duration=${duration}`;
    setLaunched(true);
  };

  const handleSave = async () => {
    if (!selectedPatient) return alert("Please select a patient.");
    setSaving(true);
    await saveSession({
      patientId:       selectedPatient.id,
      patientName:     selectedPatient.name,
      therapistId,
      exerciseName:    exercise,
      durationMinutes: duration,
      repsCompleted:   reps,
      notes,
    });
    setSaving(false);
    setSaved(true);
    setTimeout(() => { setSaved(false); onSaved(); }, 1500);
  };

  return (
    <div className="session-view">
      <h1>New Exercise Session</h1>
      <p className="subtitle">Configure the session, launch the game, then save the record.</p>

      <div className="session-form">
        {/* Patient Selector */}
        <div className="field-group">
          <label>Patient</label>
          <select
            value={selectedPatient?.id || ""}
            onChange={(e) => setSelectedPatient(patients.find((p) => p.id === e.target.value))}
          >
            <option value="">— Select Patient —</option>
            {patients.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>

        {/* Exercise */}
        <div className="field-group">
          <label>Exercise</label>
          <select value={exercise} onChange={(e) => setExercise(e.target.value)}>
            {EXERCISES.map((ex) => <option key={ex}>{ex}</option>)}
          </select>
        </div>

        {/* Duration + Reps */}
        <div className="field-row">
          <div className="field-group">
            <label>Duration (minutes)</label>
            <input type="number" min={1} max={120} value={duration} onChange={(e) => setDuration(+e.target.value)} />
          </div>
          <div className="field-group">
            <label>Target Reps</label>
            <input type="number" min={1} max={500} value={reps} onChange={(e) => setReps(+e.target.value)} />
          </div>
        </div>

        {/* Notes */}
        <div className="field-group">
          <label>Session Notes</label>
          <textarea
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Patient tolerance, observations, modifications…"
          />
        </div>

        {/* Actions */}
        <div className="session-actions">
          <button className="btn-launch" onClick={launchUnity}>
            🎮 Launch Unity Game
          </button>
          <button className="btn-primary" onClick={handleSave} disabled={saving || saved}>
            {saved ? "✓ Saved!" : saving ? "Saving…" : "Save Session Record"}
          </button>
        </div>

        {launched && (
          <div className="launch-notice">
            <span>⚡</span> Game launched! If nothing opened, make sure the PhysioSync Unity app is installed on this machine.
          </div>
        )}
      </div>
    </div>
  );
}
