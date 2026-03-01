// components/PipelineRunner.jsx
// Full pipeline: server health check → template selection → mocap recording
// → DTW analysis → save to Firestore → show results
import { useState, useEffect, useRef } from "react";
import {
  checkServerHealth,
  listTemplates,
  listPatientFiles,
  startRecording,
  getRecordingStatus,
  stopRecording,
  runAnalysis,
} from "../api/localServer";
import { saveAnalysisResult } from "../firebase/db";

// ── Step constants ────────────────────────────────────────────────────────────
const STEP = {
  SERVER_CHECK: 0,
  CONFIGURE:    1,
  RECORDING:    2,
  ANALYZING:    3,
  RESULTS:      4,
};

// ── Score ring color ──────────────────────────────────────────────────────────
function scoreColor(score) {
  if (score >= 80) return "#00e5c3";
  if (score >= 50) return "#0090ff";
  return "#ff4b6e";
}

// ── Circular score ring ───────────────────────────────────────────────────────
function ScoreRing({ score }) {
  const r = 54, circ = 2 * Math.PI * r;
  const offset = circ - (score / 100) * circ;
  const color = scoreColor(score);
  return (
    <div className="score-ring-wrap">
      <svg width="140" height="140" viewBox="0 0 140 140">
        <circle cx="70" cy="70" r={r} fill="none" stroke="#232a3a" strokeWidth="10" />
        <circle
          cx="70" cy="70" r={r} fill="none"
          stroke={color} strokeWidth="10"
          strokeDasharray={circ} strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 70 70)"
          style={{ transition: "stroke-dashoffset 1s ease" }}
        />
      </svg>
      <div className="score-ring-text">
        <span className="score-number" style={{ color }}>{score}</span>
        <span className="score-label">/100</span>
      </div>
    </div>
  );
}

// ── Grade pill ────────────────────────────────────────────────────────────────
function GradePill({ label, value, max = 10 }) {
  const pct = (value / max) * 100;
  const color = pct >= 80 ? "#00e5c3" : pct >= 50 ? "#0090ff" : "#ff4b6e";
  return (
    <div className="grade-pill">
      <span className="grade-pill-label">{label}</span>
      <div className="grade-pill-bar-bg">
        <div className="grade-pill-bar" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="grade-pill-val" style={{ color }}>{value}/{max}</span>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function PipelineRunner({ patient, patients, therapistId, onSaved }) {
  const [step, setStep]               = useState(STEP.SERVER_CHECK);
  const [serverOk, setServerOk]       = useState(null);       // null|true|false
  const [templates, setTemplates]     = useState([]);
  const [patientFiles, setPatientFiles] = useState([]);
  
  // Config
  const [selectedPatient, setSelectedPatient] = useState(patient || null);
  const [template, setTemplate]       = useState("");
  const [exerciseName, setExerciseName] = useState("Wrist Rotation");
  const [duration, setDuration]       = useState(8);
  const [sensitivity, setSensitivity] = useState(3.0);
  const [shapeTol, setShapeTol]       = useState(0.20);

  // Recording
  const [recordStatus, setRecordStatus] = useState(null); // { state, message, output_file }
  const pollRef = useRef(null);

  // Analysis
  const [analyzing, setAnalyzing]     = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [result, setResult]           = useState(null);

  // Saving
  const [saving, setSaving]           = useState(false);
  const [saved, setSaved]             = useState(false);

  // ── Server health check on mount ─────────────────────────────────────────
  useEffect(() => {
    checkServerHealth()
      .then(() => {
        setServerOk(true);
        // Pre-load templates and patient files
        listTemplates().then((d) => { setTemplates(d.templates); if (d.templates[0]) setTemplate(d.templates[0]); });
        listPatientFiles().then((d) => setPatientFiles(d.files));
        setStep(STEP.CONFIGURE);
      })
      .catch(() => setServerOk(false));
  }, []);

  // ── Polling during recording ──────────────────────────────────────────────
  useEffect(() => {
    if (step !== STEP.RECORDING) return;
    pollRef.current = setInterval(async () => {
      try {
        const status = await getRecordingStatus();
        setRecordStatus(status);
        if (status.state === "done" || status.state === "error") {
          clearInterval(pollRef.current);
          if (status.state === "done") {
            // Refresh file list and jump to analysis
            const d = await listPatientFiles();
            setPatientFiles(d.files);
            setStep(STEP.ANALYZING);
            runDTW(status.output_file);
          }
        }
      } catch (_) {}
    }, 1000);
    return () => clearInterval(pollRef.current);
  }, [step]);

  // ── Start recording ───────────────────────────────────────────────────────
  const handleStartRecording = async () => {
    if (!selectedPatient) return alert("Please select a patient first.");
    if (!template)         return alert("Please select an exercise template.");
    setStep(STEP.RECORDING);
    setRecordStatus({ state: "recording", message: `Recording for ${duration}s…`, output_file: null });
    await startRecording(duration);
  };

  // ── Run DTW analysis ──────────────────────────────────────────────────────
  const runDTW = async (patientFile) => {
    setAnalyzing(true);
    setAnalysisError("");
    try {
      const res = await runAnalysis(patientFile, template, sensitivity, shapeTol);
      setResult(res);
      setStep(STEP.RESULTS);
    } catch (e) {
      setAnalysisError(e.message);
      setStep(STEP.CONFIGURE); // fall back so they can retry
    } finally {
      setAnalyzing(false);
    }
  };

  // ── Manually trigger analysis on an existing file ─────────────────────────
  const [manualFile, setManualFile] = useState("");
  const handleManualAnalyze = async () => {
    if (!manualFile) return alert("Select a recorded file.");
    if (!template)  return alert("Select a template.");
    setStep(STEP.ANALYZING);
    await runDTW(manualFile);
  };

  // ── Save to Firestore ─────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!result || !selectedPatient) return;
    setSaving(true);
    try {
      await saveAnalysisResult({
        patientId:      selectedPatient.id,
        patientName:    selectedPatient.name,
        therapistId,
        exerciseName,
        templateFile:   result.template_file,
        patientFile:    result.patient_file,
        score:          result.score,
        global_rmse:    result.global_rmse,
        axis_rmse:      result.axis_rmse,
        rom_ratio:      result.rom_ratio,
        rom_ratios:     result.rom_ratios,
        rom_axis_grades: result.rom_axis_grades,
        avg_rom_grade:  result.avg_rom_grade,
        shape_grade:    result.shape_grade,
        report_text:    result.report_text,
        plot_image_b64: result.plot_image_b64,
      });
      setSaved(true);
      setTimeout(() => onSaved?.(), 1500);
    } catch (e) {
      alert("Failed to save: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  // ─────────────────────────────────────────────────────────────────────────
  //  RENDER
  // ─────────────────────────────────────────────────────────────────────────

  // Step 0: Server check
  if (step === STEP.SERVER_CHECK) {
    return (
      <div className="pipeline-view">
        <h1>Run Analysis Pipeline</h1>
        <div className="server-check-card">
          <div className="spinner" style={{ margin: "0 auto 16px" }} />
          <p>Connecting to local Python server on <code>localhost:5050</code>…</p>
          {serverOk === false && (
            <div className="pipeline-error" style={{ marginTop: 16 }}>
              <strong>Cannot reach local server.</strong>
              <p>Make sure you have run: <code>python server.py</code> in your project folder.</p>
              <button className="btn-primary" style={{ marginTop: 12 }} onClick={() => {
                setServerOk(null);
                checkServerHealth()
                  .then(() => { setServerOk(true); setStep(STEP.CONFIGURE); })
                  .catch(() => setServerOk(false));
              }}>Retry Connection</button>
            </div>
          )}
        </div>
      </div>
    );
  }

  // Step 1: Configure
  if (step === STEP.CONFIGURE) {
    return (
      <div className="pipeline-view">
        <div className="pipeline-header">
          <div>
            <h1>Run Analysis Pipeline</h1>
            <p className="subtitle">Configure, record motion, and evaluate in one flow.</p>
          </div>
          <div className="server-badge">
            <span className="dot-green" /> Server Connected
          </div>
        </div>

        {analysisError && (
          <div className="pipeline-error" style={{ marginBottom: 20 }}>
            <strong>Analysis failed:</strong> {analysisError}
          </div>
        )}

        <div className="pipeline-grid">
          {/* Left: New Recording */}
          <div className="pipeline-card">
            <h3>🎥 New Recording</h3>
            <p className="card-desc">Record live motion via RealSense camera then analyze automatically.</p>

            <div className="field-group">
              <label>Patient</label>
              <select value={selectedPatient?.id || ""} onChange={(e) =>
                setSelectedPatient(patients?.find((p) => p.id === e.target.value))
              }>
                <option value="">— Select Patient —</option>
                {patients?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>

            <div className="field-group">
              <label>Exercise Name</label>
              <input value={exerciseName} onChange={(e) => setExerciseName(e.target.value)} />
            </div>

            <div className="field-group">
              <label>Template File</label>
              <select value={template} onChange={(e) => setTemplate(e.target.value)}>
                <option value="">— Select Template —</option>
                {templates.map((t) => <option key={t}>{t}</option>)}
              </select>
              {templates.length === 0 && (
                <p className="field-hint">Put .xlsx templates in the <code>templates/</code> folder, then refresh.</p>
              )}
            </div>

            <div className="field-row">
              <div className="field-group">
                <label>Duration (s)</label>
                <input type="number" min={3} max={60} value={duration} onChange={(e) => setDuration(+e.target.value)} />
              </div>
              <div className="field-group">
                <label>Sensitivity</label>
                <input type="number" step={0.5} min={0.5} max={10} value={sensitivity} onChange={(e) => setSensitivity(+e.target.value)} />
              </div>
            </div>

            <div className="field-group">
              <label>Shape Tolerance (m) — e.g. 0.20 = 20cm</label>
              <input type="number" step={0.05} min={0.05} max={1} value={shapeTol} onChange={(e) => setShapeTol(+e.target.value)} />
            </div>

            <button className="btn-launch" style={{ marginTop: 8, width: "100%" }} onClick={handleStartRecording}>
              🎥 Start Recording
            </button>
          </div>

          {/* Right: Analyze existing file */}
          <div className="pipeline-card">
            <h3>📂 Analyze Existing File</h3>
            <p className="card-desc">Pick a previously recorded file and run analysis without re-recording.</p>

            <div className="field-group">
              <label>Patient</label>
              <select value={selectedPatient?.id || ""} onChange={(e) =>
                setSelectedPatient(patients?.find((p) => p.id === e.target.value))
              }>
                <option value="">— Select Patient —</option>
                {patients?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>

            <div className="field-group">
              <label>Recorded File</label>
              <select value={manualFile} onChange={(e) => setManualFile(e.target.value)}>
                <option value="">— Select File —</option>
                {patientFiles.map((f) => <option key={f}>{f}</option>)}
              </select>
            </div>

            <div className="field-group">
              <label>Template File</label>
              <select value={template} onChange={(e) => setTemplate(e.target.value)}>
                <option value="">— Select Template —</option>
                {templates.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>

            <div className="field-group">
              <label>Exercise Name</label>
              <input value={exerciseName} onChange={(e) => setExerciseName(e.target.value)} />
            </div>

            <button className="btn-primary" style={{ marginTop: "auto", width: "100%" }} onClick={handleManualAnalyze}>
              ▶ Run Analysis
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Step 2: Recording in progress
  if (step === STEP.RECORDING) {
    const isDone  = recordStatus?.state === "done";
    const isError = recordStatus?.state === "error";
    return (
      <div className="pipeline-view">
        <h1>Recording Motion…</h1>
        <div className="recording-card">
          <div className={`rec-indicator ${isDone ? "done" : isError ? "error" : "active"}`}>
            {isDone ? "✓" : isError ? "✗" : "●"}
          </div>
          <p className="rec-message">{recordStatus?.message || "Initializing camera…"}</p>
          {!isDone && !isError && (
            <button className="btn-secondary" style={{ marginTop: 16 }} onClick={async () => {
              await stopRecording();
              setStep(STEP.CONFIGURE);
            }}>
              Stop Early
            </button>
          )}
          {isError && (
            <button className="btn-primary" style={{ marginTop: 16 }} onClick={() => setStep(STEP.CONFIGURE)}>
              Back to Configure
            </button>
          )}
        </div>
      </div>
    );
  }

  // Step 3: Analyzing
  if (step === STEP.ANALYZING) {
    return (
      <div className="pipeline-view">
        <h1>Running DTW Analysis…</h1>
        <div className="recording-card">
          <div className="spinner" style={{ margin: "0 auto 20px" }} />
          <p>Comparing patient motion to expert template using multivariate DTW.</p>
          <p className="muted" style={{ marginTop: 8, fontSize: 13 }}>This may take 5–15 seconds.</p>
        </div>
      </div>
    );
  }

  // Step 4: Results
  if (step === STEP.RESULTS && result) {
    const r = result;
    return (
      <div className="pipeline-view">
        <div className="results-topbar">
          <div>
            <h1>Analysis Complete</h1>
            <p className="subtitle">{exerciseName} — {selectedPatient?.name}</p>
          </div>
          <div className="results-actions">
            <button className="btn-secondary" onClick={() => { setResult(null); setStep(STEP.CONFIGURE); }}>
              ← New Analysis
            </button>
            <button className="btn-primary" onClick={handleSave} disabled={saving || saved}>
              {saved ? "✓ Saved to Records" : saving ? "Saving…" : "💾 Save to Patient Records"}
            </button>
          </div>
        </div>

        <div className="results-grid">
          {/* Score */}
          <div className="result-card center">
            <h3>Overall Score</h3>
            <ScoreRing score={r.score} />
          </div>

          {/* Grades */}
          <div className="result-card">
            <h3>Clinical Grades</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
              <GradePill label="ROM Grade"   value={Math.round(r.avg_rom_grade)} />
              <GradePill label="Shape Grade" value={r.shape_grade} />
              <GradePill label="ROM X-axis"  value={r.rom_axis_grades[0]} />
              <GradePill label="ROM Y-axis"  value={r.rom_axis_grades[1]} />
              <GradePill label="ROM Z-axis"  value={r.rom_axis_grades[2]} />
            </div>
          </div>

          {/* RMSE */}
          <div className="result-card">
            <h3>Error Metrics</h3>
            <table className="mini-table">
              <tbody>
                <tr><td>Global RMSE</td><td className="val">{r.global_rmse} m</td></tr>
                <tr><td>RMSE X</td><td className="val">{r.axis_rmse.x} m</td></tr>
                <tr><td>RMSE Y</td><td className="val">{r.axis_rmse.y} m</td></tr>
                <tr><td>RMSE Z</td><td className="val">{r.axis_rmse.z} m</td></tr>
                <tr><td>ROM Ratio</td><td className="val">{(r.rom_ratio * 100).toFixed(1)}%</td></tr>
              </tbody>
            </table>
          </div>

          {/* Therapist report */}
          <div className="result-card span-2">
            <h3>Therapist Report</h3>
            <pre className="report-pre">{r.report_text}</pre>
          </div>

          {/* Plot image */}
          <div className="result-card span-3">
            <h3>3D Trajectory Comparison</h3>
            <img
              src={`data:image/png;base64,${r.plot_image_b64}`}
              alt="DTW comparison plot"
              className="result-plot"
            />
          </div>
        </div>
      </div>
    );
  }

  return null;
}
