// components/PipelineRunner.jsx
// Full pipeline: server health check → template selection → mocap recording
// → DTW analysis → save to Firestore → show results
import { useState, useEffect, useRef, useCallback } from "react";
import { ChevronDown } from "lucide-react";
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
import "./ProgressTable.css";

// ── Step constants ────────────────────────────────────────────────────────────
const STEP = {
  SERVER_CHECK: 0,
  CONFIGURE:    1,
  RECORDING:    2,
  ANALYZING:    3,
  RESULTS:      4,
};

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
  const [activeSessionKpiBreakdown, setActiveSessionKpiBreakdown] = useState(null);

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
      setActiveSessionKpiBreakdown(null);
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

        // Therapist detail metrics (session-level aggregates)
        axis_rmse:           d(result.axis_rmse, null),
        rom_axis_grades:     d(result.rom_axis_grades, null),
        ref_peak_velocity:   d(result.ref_peak_velocity, null),
        pat_peak_velocity:   d(result.pat_peak_velocity, null),
        ref_mean_velocity:   d(result.ref_mean_velocity, null),
        pat_mean_velocity:   d(result.pat_mean_velocity, null),
        pat_velocity_rmse:   d(result.pat_velocity_rmse, null),
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
    const scoreColor = (v) => { const n = Number(v); return n >= 7 ? '#00e5c3' : n >= 4 ? '#f39c12' : '#ff4b6e'; };
    const attempts = Array.isArray(r.per_attempt_metrics) ? r.per_attempt_metrics : [];

    // ── Helpers matching ProgressTable ────────────────────────────────────
    const axisKeys = ['X', 'Y', 'Z'];

    const pickAxisTriplet = (block) => {
      if (!block || typeof block !== 'object') return null;
      const out = {}; let any = false;
      for (const k of axisKeys) {
        const v = block[k] ?? block[k.toLowerCase()];
        if (v != null && v !== '') { out[k] = Number(v); any = true; } else { out[k] = null; }
      }
      return any ? out : null;
    };

    const avgTriplet = (rows, field) => {
      const sums = { X: 0, Y: 0, Z: 0 }, counts = { X: 0, Y: 0, Z: 0 };
      for (const row of rows) {
        const b = row?.[field]; if (!b) continue;
        for (const k of axisKeys) { const v = b[k]; if (v != null && !Number.isNaN(Number(v))) { sums[k] += Number(v); counts[k]++; } }
      }
      const out = {}; let any = false;
      for (const k of axisKeys) { out[k] = counts[k] ? Math.round((sums[k] / counts[k]) * 10000) / 10000 : null; if (out[k] != null) any = true; }
      return any ? out : null;
    };

    const avgScalar = (rows, field) => {
      const vals = rows.map(row => row?.[field]).filter(v => v != null && !Number.isNaN(Number(v))).map(Number);
      return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
    };

    const axisRmse      = pickAxisTriplet(r.axis_rmse)      || avgTriplet(attempts, 'axis_rmse');
    const romAxisGrades = pickAxisTriplet(r.rom_axis_grades) || avgTriplet(attempts, 'rom_axis_grades');
    const tempoMetrics  = (() => {
      const direct = { pat_velocity_rmse: r.pat_velocity_rmse, ref_peak_velocity: r.ref_peak_velocity, pat_peak_velocity: r.pat_peak_velocity, ref_mean_velocity: r.ref_mean_velocity, pat_mean_velocity: r.pat_mean_velocity };
      if (Object.values(direct).some(v => v != null)) return { pat_velocity_rmse: direct.pat_velocity_rmse != null ? Number(direct.pat_velocity_rmse) : null, ref_peak_velocity: direct.ref_peak_velocity != null ? Number(direct.ref_peak_velocity) : null, pat_peak_velocity: direct.pat_peak_velocity != null ? Number(direct.pat_peak_velocity) : null, ref_mean_velocity: direct.ref_mean_velocity != null ? Number(direct.ref_mean_velocity) : null, pat_mean_velocity: direct.pat_mean_velocity != null ? Number(direct.pat_mean_velocity) : null };
      const fields = ['pat_velocity_rmse','ref_peak_velocity','pat_peak_velocity','ref_mean_velocity','pat_mean_velocity'];
      const acc = {}; for (const f of fields) acc[f] = [];
      for (const a of attempts) { for (const f of fields) { const v = a[f]; if (v != null) acc[f].push(Number(v)); } }
      const out = {}; let any = false;
      for (const f of fields) { out[f] = acc[f].length ? Math.round((acc[f].reduce((s, x) => s + x, 0) / acc[f].length) * 10000) / 10000 : null; if (out[f] != null) any = true; }
      return any ? out : null;
    })();

    const fmt = (v, d = 3) => v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d);

    const KPI_KEYS = [
      { key: 'som_grade',           label: 'SoM' },
      { key: 'rom_grade',           label: 'ROM' },
      { key: 'tempo_control_grade', label: 'Tempo' },
      { key: 'hesitation_grade',    label: 'Hesitation' },
      { key: 'tremor_grade',        label: 'Tremor' },
    ];
    const KPI_EXPANDABLE = new Set(['som_grade', 'rom_grade', 'tempo_control_grade']);

    const globalScore = Number(r.global_score ?? 0);

    // per-attempt figures
    const normalizeImg = (raw) => {
      if (!raw || typeof raw !== 'string') return null;
      const t = raw.trim();
      return t.startsWith('data:image/') ? t : `data:image/png;base64,${t}`;
    };
    const plotImage = normalizeImg(r.plot_image_b64 || r.session_attempts_plot_b64);
    const sessionPlotImage = normalizeImg(r.global_report_plot_b64 || r.session_plot_image_b64);

    return (
      <div className="pipeline-view">
        <div className="results-topbar">
          <div>
            <h1>Analysis Complete</h1>
            <p className="subtitle">{exerciseName} — {selectedPatient?.name} &nbsp;·&nbsp; {r.num_attempts || 1} attempt{r.num_attempts !== 1 ? 's' : ''}</p>
          </div>
          <div className="results-actions">
            <button className="btn-secondary" onClick={() => { setResult(null); setStep(STEP.CONFIGURE); }}>← New Analysis</button>
            <button className="btn-primary" onClick={handleSave} disabled={saving || saved}>
              {saved ? '✓ Saved to Records' : saving ? 'Saving…' : '💾 Save to Patient Records'}
            </button>
          </div>
        </div>

        {/* Uses the same detail-panel layout as ProgressTable expanded rows */}
        <div className="results-container">
          <div className="detail-panel">

            {/* ── Global Score + KPI Cards (mirrors detail-top) ── */}
            <div className="detail-top">
              <div className="score-ring-panel">
                <div className="score-ring-label">Global Score</div>
                <div className="global-score-value" style={{ color: scoreColor(globalScore) }}>
                  {globalScore.toFixed(1)}
                </div>
              </div>

              <div className="kpi-cards">
                {KPI_KEYS.map(({ key, label }) => {
                  const value = Number(r[key] ?? 0);
                  const expandable = KPI_EXPANDABLE.has(key);
                  const open = expandable && activeSessionKpiBreakdown === key;
                  return (
                    <div
                      key={key}
                      className={`kpi-card${expandable ? ' kpi-card--expandable' : ''}${open ? ' kpi-card--active' : ''}`}
                    >
                      {expandable ? (
                        <button
                          type="button"
                          className="kpi-card-main"
                          onClick={() => setActiveSessionKpiBreakdown(prev => prev === key ? null : key)}
                          aria-expanded={open}
                        >
                          <div className="kpi-label-row">
                            <span className="kpi-label">{label}</span>
                            <ChevronDown size={16} className={`kpi-chevron${open ? ' kpi-chevron--open' : ''}`} aria-hidden />
                          </div>
                          <div className="kpi-score" style={{ color: scoreColor(value) }}>{value.toFixed(1)}</div>
                        </button>
                      ) : (
                        <div className="kpi-card-main kpi-card-main--static">
                          <div className="kpi-label-row">
                            <span className="kpi-label">{label}</span>
                          </div>
                          <div className="kpi-score" style={{ color: scoreColor(value) }}>{value.toFixed(1)}</div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* ── Expandable KPI breakdown panel (mirrors kpi-breakdown-panel) ── */}
            {activeSessionKpiBreakdown && (
              <div className="kpi-breakdown-panel" style={{ marginBottom: 16 }}>
                {activeSessionKpiBreakdown === 'som_grade' && (
                  <>
                    <div className="kpi-breakdown-title">SoM Breakdown: Axis-wise shape error (m)</div>
                    {axisRmse ? (
                      <ul className="kpi-breakdown-list">
                        {axisKeys.map(axis => (
                          <li key={axis}><span>{axis}</span><span>{fmt(axisRmse[axis], 3)}</span></li>
                        ))}
                      </ul>
                    ) : <p className="kpi-breakdown-empty">No axis RMSE data for this session.</p>}
                  </>
                )}
                {activeSessionKpiBreakdown === 'rom_grade' && (
                  <>
                    <div className="kpi-breakdown-title">ROM Breakdown: Axis grades (0-10)</div>
                    {romAxisGrades ? (
                      <ul className="kpi-breakdown-list">
                        {axisKeys.map(axis => (
                          <li key={axis}><span>{axis}</span><span>{romAxisGrades[axis] != null ? Number(romAxisGrades[axis]).toFixed(1) : '—'}</span></li>
                        ))}
                      </ul>
                    ) : <p className="kpi-breakdown-empty">No per-axis ROM grades for this session.</p>}
                  </>
                )}
                {activeSessionKpiBreakdown === 'tempo_control_grade' && (
                  <>
                    <div className="kpi-breakdown-title">Tempo Breakdown: Velocity profile metrics</div>
                    {tempoMetrics ? (
                      <ul className="kpi-breakdown-list kpi-breakdown-list--stacked">
                        <li><span>Velocity RMSE</span><span>{fmt(tempoMetrics.pat_velocity_rmse, 4)}</span></li>
                        <li><span>Peak velocity (ref / patient)</span><span>{fmt(tempoMetrics.ref_peak_velocity, 4)} / {fmt(tempoMetrics.pat_peak_velocity, 4)}</span></li>
                        <li><span>Mean velocity (ref / patient)</span><span>{fmt(tempoMetrics.ref_mean_velocity, 4)} / {fmt(tempoMetrics.pat_mean_velocity, 4)}</span></li>
                      </ul>
                    ) : <p className="kpi-breakdown-empty">No velocity metrics for this session.</p>}
                  </>
                )}
              </div>
            )}

            {/* ── Per-attempt scores (mirrors scores-grid) ── */}
            {r.per_attempt_scores?.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <div className="per-attempt-header">
                  <h4>Per-Attempt Global Scores</h4>
                </div>
                <div className="scores-grid">
                  {r.per_attempt_scores.map((s, idx) => (
                    <div key={idx} className="score-card-small">
                      <div className="score-card-label">Attempt {idx + 1}</div>
                      <div className="score-card-value" style={{ color: scoreColor(s) }}>{Number(s).toFixed(1)}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ── Figures (mirrors figures-grid) ── */}
            {(sessionPlotImage || plotImage) && (
              <div className="per-attempt-section" style={{ marginTop: 8 }}>
                <div className="per-attempt-header">
                  <h4>Session Figures</h4>
                </div>
                <div className="figures-grid">
                  {sessionPlotImage && (
                    <div className="figure-card">
                      <div className="figure-title">Global Report</div>
                      <img src={sessionPlotImage} alt="Global report" className="figure-image" />
                    </div>
                  )}
                  {plotImage && (
                    <div className="figure-card">
                      <div className="figure-title">3D Trajectory Comparison</div>
                      <img src={plotImage} alt="Trajectory comparison" className="figure-image" />
                    </div>
                  )}
                </div>
              </div>
            )}

          </div>
        </div>
      </div>
    );
  }

  return null;
}
