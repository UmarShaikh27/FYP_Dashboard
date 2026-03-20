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
  const [recordingFile, setRecordingFile] = useState(null); // filename saved by shoulder_origin.py
  const [showLogs, setShowLogs]       = useState(false); // For recording logs
  const [fullLogs, setFullLogs]       = useState(""); // Full logs from server
  const pollRef = useRef(null);

  // Analysis
  const [analyzing, setAnalyzing]     = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [analysisDebugInfo, setAnalysisDebugInfo] = useState(""); // For debugging
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
            // Store the recording filename so it gets saved with the result
            setRecordingFile(status.output_file);
            // Refresh file list and jump to analysis
            const d = await listPatientFiles();
            setPatientFiles(d.files);
            setStep(STEP.ANALYZING);
            setAnalysisDebugInfo("Starting DTW analysis...");
            // Await the DTW analysis to ensure it completes before rendering
            // (error handling is done inside runDTW)
            await runDTW(status.output_file);
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
    setAnalysisDebugInfo("Calling DTW analysis endpoint...");
    
    try {
      // Add a timeout so we don't hang forever
      const analysisPromise = runAnalysis(patientFile, template, sensitivity, shapeTol);
      const timeoutPromise = new Promise((_, reject) =>
        setTimeout(() => reject(new Error("Analysis took too long (>30s) — server may be unresponsive")), 30000)
      );
      
      const res = await Promise.race([analysisPromise, timeoutPromise]);
      
      // Verify response has all required fields
      if (!res || typeof res !== 'object') {
        throw new Error("Invalid response from server: not an object");
      }
      
      if (res.error) {
        throw new Error(res.error);
      }
      
      console.log("DTW analysis successful:", res);
      setAnalysisDebugInfo("Analysis complete, displaying results...");
      setResult(res);
      setStep(STEP.RESULTS);
    } catch (e) {
      console.error("DTW analysis error:", e);
      const errorMsg = e.message || "Unknown error during analysis";
      setAnalysisError(errorMsg);
      setAnalysisDebugInfo("Error: " + errorMsg);
      setStep(STEP.CONFIGURE); // fall back so they can retry
      throw e; // Re-throw so the calling code knows it failed
    } finally {
      setAnalyzing(false);
    }
  };

  // ── Manually trigger analysis on an existing file ─────────────────────────
  const [manualFile, setManualFile] = useState("");
  const handleManualAnalyze = async () => {
    if (!manualFile) return alert("Select a recorded file.");
    if (!template)  return alert("Select a template.");
    setRecordingFile(manualFile);
    setStep(STEP.ANALYZING);
    setAnalysisDebugInfo("Starting DTW analysis on selected file...");
    try {
      await runDTW(manualFile);
    } catch (err) {
      console.error("Error in manual analysis:", err);
      // Error handling is already done in runDTW, which sets the error state
    }
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
        recordingFile:  recordingFile || result.patient_file,
        score:          result.score,
        global_rmse:    result.global_rmse,
        axis_rmse:      result.axis_rmse,
        rom_ratio:      result.rom_ratio,
        rom_ratios:     result.rom_ratios,
        rom_axis_grades: result.rom_axis_grades,
        avg_rom_grade:  result.avg_rom_grade,
        shape_grade:    result.shape_grade,
        sparc:          result.sparc,
        sparc_grades:   result.sparc_grades,
        report_text:    result.report_text,
        plot_image_b64: result.plot_image_b64,
        excel_file_b64: result.excel_file_b64,  // Base64-encoded Excel file
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
    const isGrace = recordStatus?.state === "grace";

    const fetchLogs = async () => {
      try {
        const data = await fetch("http://localhost:5050/mocap/logs").then(r => r.json());
        setFullLogs(data.full_stderr || data.stdout || "No logs available.");
        setShowLogs(true);
      } catch { setFullLogs("Could not fetch logs from server."); setShowLogs(true); }
    };

    return (
      <div className="pipeline-view">
        <h1>{isError ? "Recording Failed" : isGrace ? "Get Ready…" : isDone ? "Recording Complete" : "Recording Motion…"}</h1>
        <div className="recording-card">
          <div className={`rec-indicator ${isDone ? "done" : isError ? "error" : "active"}`}>
            {isDone ? "✓" : isError ? "✗" : isGrace ? "⏱" : "●"}
          </div>
          <p className="rec-message">{recordStatus?.message || "Initializing camera…"}</p>

          {isError && (
            <div className="error-box" style={{ marginTop: 16 }}>
              <pre className="report-pre small" style={{ textAlign: "left", color: "#ff4b6e", maxHeight: 300, overflowY: "auto" }}>
                {recordStatus.message}
              </pre>
              <div style={{ display: "flex", gap: 10, marginTop: 12, justifyContent: "center", flexWrap: "wrap" }}>
                <button className="btn-secondary" onClick={fetchLogs}>📋 View Full Logs</button>
                <button className="btn-primary" onClick={() => setStep(STEP.CONFIGURE)}>← Back to Configure</button>
              </div>
              {showLogs && (
                <pre className="report-pre small" style={{ marginTop: 12, maxHeight: 400, overflowY: "auto", color: "var(--text-muted)" }}>
                  {fullLogs}
                </pre>
              )}
            </div>
          )}

          {!isDone && !isError && (
            <button className="btn-secondary" style={{ marginTop: 16 }} onClick={async () => {
              await stopRecording();
              setStep(STEP.CONFIGURE);
            }}>
              Stop Early
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
          
          {analysisDebugInfo && (
            <p className="muted" style={{ marginTop: 12, fontSize: 11, fontFamily: "monospace", color: "#0090ff" }}>
              {analysisDebugInfo}
            </p>
          )}
          
          {analysisError && (
            <div className="error-box" style={{ marginTop: 16 }}>
              <p style={{ color: "#ff4b6e", fontWeight: "bold" }}>⚠ Analysis Error:</p>
              <pre className="report-pre small" style={{ textAlign: "left", color: "#ff4b6e", maxHeight: 200, overflowY: "auto" }}>
                {analysisError}
              </pre>
              <p className="muted" style={{ marginTop: 12, fontSize: 12 }}>Try again or use the "Analyze Existing File" option.</p>
              <button className="btn-primary" style={{ marginTop: 12 }} onClick={() => setStep(STEP.CONFIGURE)}>
                ← Back to Configure
              </button>
            </div>
          )}
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
            {r.excel_file_b64 && (
              <button 
                className="btn-secondary"
                onClick={() => {
                  // Convert base64 to blob and trigger download
                  const binaryString = atob(r.excel_file_b64);
                  const bytes = new Uint8Array(binaryString.length);
                  for (let i = 0; i < binaryString.length; i++) {
                    bytes[i] = binaryString.charCodeAt(i);
                  }
                  const blob = new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
                  const url = window.URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `${exerciseName}_${selectedPatient?.name || 'motion'}_${new Date().toISOString().slice(0, 10)}.xlsx`;
                  document.body.appendChild(a);
                  a.click();
                  window.URL.revokeObjectURL(url);
                  document.body.removeChild(a);
                }}
              >
                📥 Download Excel
              </button>
            )}
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
              {r.sparc_grades && <>
                <div className="grade-section-divider">Smoothness (SPARC)</div>
                <GradePill label="Overall"    value={r.sparc_grades.total} />
                <GradePill label="Choppiness" value={r.sparc_grades.choppiness} />
                <GradePill label="Tremor"     value={r.sparc_grades.tremor} />
              </>}
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
                {r.sparc && <>
                  <tr><td colSpan={2} style={{paddingTop:10,color:"var(--text-muted)",fontSize:12}}>SPARC Metrics</td></tr>
                  <tr><td>Overall SPARC</td><td className="val">{r.sparc.total}</td></tr>
                  <tr><td>Vel. RMSE</td><td className="val">{r.sparc.velocity_rmse} m/s</td></tr>
                  <tr><td>Peak Velocity</td><td className="val">{r.sparc.peak_velocity} m/s</td></tr>
                </>}
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
