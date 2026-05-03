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
  runPipelineAnalysis,
} from "../api/localServer";
import { saveAnalysisResult } from "../firebase/db";

// ── Step constants ────────────────────────────────────────────────────────────
const STEP = {
  SERVER_CHECK: 0,
  CONFIGURE:    1,
  RECORDING:    2,
  ANALYZING:    3,
  RESULTS:      4,
};// ── Circular score ring ───────────────────────────────────────────────────────
function ScoreRing({ score, max = 10 }) {
  const r = 54, circ = 2 * Math.PI * r;
  const offset = circ - (Math.max(0, Math.min(score, max)) / max) * circ;
  
  const scoreColor = (s) => s >= 7 ? '#00e5c3' : s >= 4 ? '#f39c12' : '#ff4b6e';
  const color = scoreColor(score);
  
  return (
    <div className="score-ring-wrap" style={{ display: 'flex', justifyContent: 'center', margin: '20px 0' }}>
      <div style={{ position: 'relative', width: '140px', height: '140px' }}>
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
        <div style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
          <span style={{ color, fontSize: '32px', fontWeight: 'bold' }}>{Number(score).toFixed(1)}</span>
          <span style={{ color: 'var(--text-muted)', fontSize: '14px' }}>/{max}</span>
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function PipelineRunner({ patient, patients, therapistId, onSaved }) {
  const [step, setStep]               = useState(STEP.SERVER_CHECK);
  const [serverOk, setServerOk]       = useState(null);
  const [templates, setTemplates]     = useState([]);
  const [patientFiles, setPatientFiles] = useState([]);

  // Config
  const [selectedPatient, setSelectedPatient] = useState(patient || null);
  const [template, setTemplate]       = useState("");
  const [exerciseName, setExerciseName] = useState("");
  const [duration, setDuration]       = useState(8);
  const [sensitivity, setSensitivity] = useState(3.0);
  const [shapeTol, setShapeTol]       = useState(0.20);

  // Recording
  const [recordStatus, setRecordStatus] = useState(null);
  const [recordingFile, setRecordingFile] = useState(null);
  const [showLogs, setShowLogs]       = useState(false);
  const [fullLogs, setFullLogs]       = useState("");
  const pollRef = useRef(null);

  // Analysis
  const [analyzing, setAnalyzing]     = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [result, setResult]           = useState(null);

  // Saving
  const [saving, setSaving]           = useState(false);
  const [saved, setSaved]             = useState(false);

  // Gamified session flag
  const [isGamified, setIsGamified]   = useState(false);

  // Analyze existing file
  const [manualFile, setManualFile]   = useState("");

  // ── Server health check on mount ─────────────────────────────────────────
  useEffect(() => {
    checkServerHealth()
      .then(() => {
        setServerOk(true);
        listTemplates().then((d) => { setTemplates(d.templates); });
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
            setRecordingFile(status.output_file);
            const d = await listPatientFiles();
            setPatientFiles(d.files);
            setStep(STEP.ANALYZING);
            if (isGamified && status.result) {
              // Gamified path: server already ran the full pipeline — use its result
              setResult(status.result);
              setStep(STEP.RESULTS);
            } else {
              // Normal path: trigger client-side analysis
              try { await runDTW(status.output_file); }
              catch (err) { console.error("Analysis failed:", err); }
            }
          }
        }
      } catch (_) {}
    }, 1000);
    return () => clearInterval(pollRef.current);
  }, [step, isGamified]);

  // ── Exercise → template auto-map ─────────────────────────────────────────
  const setExercise = (ex) => {
    setExerciseName(ex);
    if (ex === "Eight Tracing")    setTemplate("8_tracing_right_wrist_template.xlsx");
    else if (ex === "circumduction") setTemplate("Circumduction_right_wrist_template.xlsx");
    else if (ex === "flexion")     setTemplate("Flexion_2kg_right_wrist_template.xlsx");
    else setTemplate("");
  };

  // ── Start recording ───────────────────────────────────────────────────────
  const handleStartRecording = async (gamified = false) => {
    if (!selectedPatient) return alert("Please select a patient first.");
    if (!template)         return alert("Please select an exercise template.");
    setIsGamified(gamified);
    setStep(STEP.RECORDING);
    setRecordStatus({
      state: "recording",
      message: gamified
        ? `Unity launching… press Start in the game when ready. Recording for ${duration}s.`
        : `Recording for ${duration}s — press SPACE in camera window to begin.`,
      output_file: null,
    });
    if (gamified) {
      try {
        const res = await fetch("http://localhost:5000/mocap/unity_start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ duration, exercise: exerciseName, arm: "right" }),
        });
        const data = await res.json();
        if (!res.ok || data.error) alert(data.error || "Error starting Unity session");
      } catch (err) { alert("Failed to connect to backend server"); }
    } else {
      await startRecording(duration);
    }
  };

  // ── Run analysis ──────────────────────────────────────────────────────────
  const runDTW = async (patientFile) => {
    setAnalyzing(true);
    setAnalysisError("");
    try {
      const analysisPromise = runPipelineAnalysis(patientFile, template, exerciseName, null, null);
      const timeoutPromise  = new Promise((_, reject) =>
        setTimeout(() => reject(new Error("Analysis took too long (>120s)")), 120000)
      );
      const res = await Promise.race([analysisPromise, timeoutPromise]);
      if (!res || typeof res !== "object") throw new Error("Invalid response from server");
      if (res.error) throw new Error(res.error);
      console.log("Pipeline result:", res);
      setResult(res);
      setStep(STEP.RESULTS);
    } catch (e) {
      console.error("Analysis error:", e);
      setAnalysisError(e.message || "Unknown error during analysis");
      setStep(STEP.CONFIGURE);
      throw e;
    } finally {
      setAnalyzing(false);
    }
  };

  const handleManualAnalyze = async () => {
    if (!manualFile) return alert("Select a recorded file.");
    if (!template)   return alert("Select a template.");
    setRecordingFile(manualFile);
    setStep(STEP.ANALYZING);
    try { await runDTW(manualFile); }
    catch (err) { console.error("Error in manual analysis:", err); }
  };

  // ── Save to Firestore ─────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!result || !selectedPatient) return;
    setSaving(true);
    const d = (v, fb = null) => v !== undefined ? v : fb;
    try {
      await saveAnalysisResult({
        patientId:    selectedPatient.id,
        patientName:  selectedPatient.name,
        therapistId,
        exerciseName,
        exercise_type:    d(result.exercise_type, exerciseName),
        templateFile:     d(result.template_file, ""),
        patientFile:      d(result.patient_file, ""),
        recordingFile:    recordingFile || d(result.patient_file, ""),

        // All scores out of 10
        global_score:          d(result.global_score, 0),
        dtw_score:             d(result.dtw_score, 0),
        som_grade:             d(result.som_grade, 0),
        rom_grade:             d(result.rom_grade, 0),
        tempo_control_grade:   d(result.tempo_control_grade, 0),
        hesitation_grade:      d(result.hesitation_grade, 0),
        tremor_grade:          d(result.tremor_grade, 0),

        // Attempt breakdown
        num_attempts:          d(result.num_attempts, 1),
        per_attempt_scores:    d(result.per_attempt_scores, []),
        per_attempt_metrics:   d(result.per_attempt_metrics, []),
        attempt_progression:   d(result.attempt_progression, {}),
        weights_config:        d(result.weights_config, {}),

        // Plots
        session_attempts_plot_b64: d(result.session_attempts_plot_b64, ""),
        global_report_plot_b64:    d(result.global_report_plot_b64, ""),
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
          <p>Connecting to local Python server on <code>localhost:5000</code>…</p>
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
            <h1>Launch Physiotherapy Session</h1>
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
              <label>Exercise Type</label>
              <select value={exerciseName} onChange={(e) => setExercise(e.target.value)}>
                <option value="">— Select Exercise —</option>
                <option value="Eight Tracing">Eight Tracing</option>
                <option value="circumduction">Circumduction</option>
                <option value="flexion">Flexion</option>
              </select>
            </div>

            <div className="field-group">
              <label>Template File (Auto-Selected)</label>
              <input value={template} readOnly disabled style={{ background: '#1a2030', color: '#6b7a96', cursor: 'not-allowed', opacity: 0.8 }} />
              {!template && <p className="field-hint">Select an exercise above to auto-detect template.</p>}
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

            <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "15px" }}>
              <button className="btn-primary start" onClick={() => handleStartRecording(false)}>
                ▶ Launch Session
              </button>
              <button className="btn-primary start" onClick={() => handleStartRecording(true)} style={{ background: "#a855f7" }}>
                🎮 Launch Gamified Session
              </button>
            </div>
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
              <label>Exercise Type</label>
              <select value={exerciseName} onChange={(e) => setExercise(e.target.value)}>
                <option value="">— Select Exercise —</option>
                <option value="Eight Tracing">Eight Tracing</option>
                <option value="circumduction">Circumduction</option>
                <option value="flexion">Flexion</option>
              </select>
            </div>

            <div className="field-group">
              <label>Template File (Auto-Selected)</label>
              <input value={template} readOnly disabled style={{ background: '#1a2030', color: '#6b7a96', cursor: 'not-allowed', opacity: 0.8 }} />
              {!template && <p className="field-hint">Select an exercise above to specify the processing file.</p>}
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

    const fetchLogs = async () => {
      try {
        const data = await fetch("http://localhost:5000/mocap/logs").then(r => r.json());
        setFullLogs(data.full_stderr || data.stdout || "No logs available.");
        setShowLogs(true);
      } catch { setFullLogs("Could not fetch logs from server."); setShowLogs(true); }
    };

    const titleText = isError ? "Recording Failed"
      : isDone    ? "Recording Complete"
      : isGamified ? "Unity Session Active"
      : "Recording Motion…";

    return (
      <div className="pipeline-view">
        <h1>{titleText}</h1>
        <div className="recording-card">
          <div className={`rec-indicator ${isDone ? "done" : isError ? "error" : "active"}`}>
            {isDone ? "✓" : isError ? "✗" : isGamified ? "🎮" : "●"}
          </div>
          <p className="rec-message">{recordStatus?.message || "Initializing camera…"}
          {isGamified && !isDone && !isError && (
            <span style={{ display: 'block', marginTop: 8, color: 'var(--text-muted)', fontSize: '0.85em' }}>
              The camera is capturing your motion. Press <strong>Start Exercise</strong> in the Unity game window to begin recording.
            </span>
          )}
          </p>
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
        <h1>Running Multi-Attempt Analysis…</h1>
        <div className="recording-card">
          <div className="spinner" style={{ margin: "0 auto 20px" }} />
          <p>Processing motion data and calculating scores for all attempts.</p>
          <p className="muted" style={{ marginTop: 8, fontSize: 13 }}>This typically takes 10–30 seconds depending on motion length.</p>
        </div>
      </div>
    );
  }

  // Step 4: Results
  if (step === STEP.RESULTS) {
    if (!result) {
      return (
        <div className="pipeline-view">
          <h1>Error: No Results</h1>
          <div className="recording-card">
            <p>The analysis completed but no results were received from the server.</p>
            <button className="btn-primary" style={{ marginTop: 16 }} onClick={() => { setResult(null); setStep(STEP.CONFIGURE); }}>
              ← Back to Configure
            </button>
          </div>
        </div>
      );
    }

    const r = result;
    const sc = (s) => { const n = Number(s); return n >= 7 ? '#00e5c3' : n >= 4 ? '#f39c12' : '#ff4b6e'; };
    const sl = (s) => { const n = Number(s); return n >= 7 ? 'Good' : n >= 4 ? 'Moderate' : 'Poor'; };

    const SESSION_SCORES = [
      { key: 'dtw_score',           label: 'DTW' },
      { key: 'som_grade',           label: 'SoM (Shape)' },
      { key: 'rom_grade',           label: 'ROM' },
      { key: 'tempo_control_grade', label: 'Tempo Control' },
      { key: 'hesitation_grade',    label: 'Hesitation' },
      { key: 'tremor_grade',        label: 'Tremor' },
    ];

    return (
      <div className="pipeline-view">
        <div className="results-topbar">
          <div>
            <h1>Analysis Complete</h1>
            <p className="subtitle">{exerciseName} — {selectedPatient?.name} &nbsp;·&nbsp; {r.num_attempts || 1} attempt{r.num_attempts !== 1 ? 's' : ''}</p>
          </div>
          <div className="results-actions">
            <button className="btn-secondary" onClick={() => { setResult(null); setStep(STEP.CONFIGURE); }}>
              ← New Analysis
            </button>
            <button className="btn-primary" onClick={handleSave} disabled={saving || saved}>
              {saved ? '✓ Saved to Records' : saving ? 'Saving…' : '💾 Save to Patient Records'}
            </button>
          </div>
        </div>

        <div className="results-container">
          
          {/* ── Global Total Score Ring ── */}
          <div className="result-card" style={{ marginBottom: '24px', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '20px', textAlign: 'center' }}>
            <h2 style={{ marginBottom: '10px' }}>Global Total Score</h2>
            <ScoreRing score={r.global_score ?? 0} max={10} />
            <p className="muted" style={{ marginTop: '10px' }}>Average overall performance across all attempts</p>
          </div>

          {/* ── Session-Level Score Blocks ── */}
          <div style={{ marginBottom: '24px' }}>
            <h2 style={{ marginBottom: '14px' }}>Session Scores — averaged across all attempts (out of 10)</h2>
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
              {SESSION_SCORES.map(({ key, label }) => {
                const val = r[key] ?? 0;
                return (
                  <div key={key} className="result-card" style={{ flex: '1 1 110px', textAlign: 'center', padding: '18px 10px' }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '8px', letterSpacing: '0.05em' }}>
                      {label}
                    </div>
                    <div style={{ fontSize: '30px', fontWeight: '800', color: sc(val), lineHeight: 1 }}>
                      {Number(val).toFixed(1)}
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>/10</div>
                    <div style={{ fontSize: '11px', marginTop: '6px', color: sc(val) }}>{sl(val)}</div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* ── Per-Attempt Global Scores ── */}
          {r.per_attempt_scores?.length > 0 && (
            <div style={{ marginBottom: '24px' }}>
              <h3 style={{ marginBottom: '12px' }}>Per-Attempt Global Scores</h3>
              <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                {r.per_attempt_scores.map((s, idx) => (
                  <div key={idx} className="result-card" style={{ flex: '1 1 80px', textAlign: 'center', padding: '14px 8px' }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '6px' }}>Attempt {idx + 1}</div>
                    <div style={{ fontSize: '26px', fontWeight: '700', color: sc(s) }}>{Number(s).toFixed(1)}</div>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>/10</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Progression Summary ── */}
          {r.attempt_progression && (
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '24px' }}>
              {[
                ['Attempts',  r.num_attempts],
                ['Avg Score', Number(r.attempt_progression.avg_score).toFixed(2)],
                ['Best',      Number(r.attempt_progression.best_attempt).toFixed(2)],
                ['Trend',     r.attempt_progression.trend],
              ].map(([lbl, val]) => (
                <div key={lbl} className="result-card" style={{ flex: '1 1 100px', textAlign: 'center' }}>
                  <div style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{lbl}</div>
                  <div style={{ fontSize: '22px', fontWeight: '700', marginTop: '6px' }}>{val}</div>
                </div>
              ))}
            </div>
          )}

          {/* ── Session Attempts Plot ── */}
          {r.session_attempts_plot_b64
            ? (
              <div className="result-card" style={{ marginBottom: '20px' }}>
                <h3 style={{ marginBottom: '12px' }}>Session Attempts — 3D Trajectory Overview</h3>
                <img src={`data:image/png;base64,${r.session_attempts_plot_b64}`} alt="Session attempts plot" className="result-plot" style={{ width: '100%' }} />
              </div>
            )
            : <p className="muted" style={{ marginBottom: '12px' }}>Session attempts plot not available.</p>
          }

          {/* ── Global Report Plot ── */}
          {r.global_report_plot_b64
            ? (
              <div className="result-card" style={{ marginBottom: '20px' }}>
                <h3 style={{ marginBottom: '12px' }}>Global Report — Score Breakdown</h3>
                <img src={`data:image/png;base64,${r.global_report_plot_b64}`} alt="Global report" className="result-plot" style={{ width: '100%' }} />
              </div>
            )
            : <p className="muted" style={{ marginBottom: '12px' }}>Global report plot not available.</p>
          }

        </div>
      </div>
    );
  }

  return null;
}
