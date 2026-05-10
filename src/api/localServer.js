// src/api/localServer.js
// Calls the Flask backend running on the therapist's local machine.
// Uses HTTPS so that the cloud-hosted Vercel frontend (HTTPS) can reach
// localhost without mixed-content blocking.

const BASE = import.meta.env.VITE_LOCAL_BACKEND_URL || "https://localhost:5000";

// ── Connection state (reactive via listeners) ────────────────────────────
let _backendConnected = false;
let _listeners = [];

export function isBackendConnected() {
  return _backendConnected;
}

/** Subscribe to connection status changes. Returns unsubscribe fn. */
export function onBackendStatusChange(cb) {
  _listeners.push(cb);
  cb(_backendConnected); // fire immediately with current state
  return () => {
    _listeners = _listeners.filter((l) => l !== cb);
  };
}

function _setConnected(val) {
  if (val !== _backendConnected) {
    _backendConnected = val;
    _listeners.forEach((cb) => cb(val));
  }
}

// ── Core fetch helper ────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    _setConnected(true);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json();
  } catch (err) {
    // Network-level failures (backend unreachable)
    if (err instanceof TypeError && err.message.includes("fetch")) {
      _setConnected(false);
    }
    throw err;
  }
}

// ── Health check (runs on an interval) ───────────────────────────────────

/** Ping the local server to check it's running */
export const checkServerHealth = async () => {
  try {
    const result = await apiFetch("/health");
    _setConnected(true);
    return result;
  } catch {
    _setConnected(false);
    throw new Error("Local backend is not reachable");
  }
};

let _healthInterval = null;

/** Start polling backend health every `ms` milliseconds (default 5 s). */
export function startHealthPolling(ms = 5000) {
  stopHealthPolling();
  // Check immediately, then on interval
  checkServerHealth().catch(() => {});
  _healthInterval = setInterval(() => {
    checkServerHealth().catch(() => {});
  }, ms);
}

/** Stop the health polling interval. */
export function stopHealthPolling() {
  if (_healthInterval) {
    clearInterval(_healthInterval);
    _healthInterval = null;
  }
}

// ── API wrappers ─────────────────────────────────────────────────────────

/** List template xlsx files in the templates/ folder */
export const listTemplates = () => apiFetch("/templates");

/** List recorded patient xlsx files in output_excel/ */
export const listPatientFiles = () => apiFetch("/files/patient");

/** Start mocap recording — duration in seconds */
export const startRecording = (duration = 8) =>
  apiFetch("/mocap/start", {
    method: "POST",
    body: JSON.stringify({ duration }),
  });

/** Poll recording status */
export const getRecordingStatus = () => apiFetch("/mocap/status");

/** Stop recording early */
export const stopRecording = () =>
  apiFetch("/mocap/stop", { method: "POST" });

/**
 * Run DTW analysis (legacy single-attempt)
 * @param {string} patientFile   - filename from output_excel/
 * @param {string} templateFile  - filename from templates/
 * @param {number} sensitivity
 * @param {number} shapeTolerance
 */
export const runAnalysis = (patientFile, templateFile, sensitivity = 3.0, shapeTolerance = 0.20) =>
  apiFetch("/analyze", {
    method: "POST",
    body: JSON.stringify({
      patient_file:    patientFile,
      template_file:   templateFile,
      sensitivity,
      shape_tolerance: shapeTolerance,
    }),
  });

/**
 * Run multi-attempt analysis with hierarchical weighted scoring
 * @param {string} patientFile   - filename from output_excel/
 * @param {string} templateFile  - filename from templates/
 * @param {string} exerciseType  - exercise identifier (e.g., "eight_tracing")
 * @param {number} nAttempts     - null for auto-detect
 * @param {object} weightsOverride - null to use default weights
 */
export const runPipelineAnalysis = (patientFile, templateFile, exerciseType = "eight_tracing", nAttempts = null, weightsOverride = null) =>
  apiFetch("/pipeline/analyze", {
    method: "POST",
    body: JSON.stringify({
      patient_file: patientFile,
      template_file: templateFile,
      exercise_type: exerciseType,
      n_attempts: nAttempts,
      weights_override: weightsOverride,
    }),
  });

/**
 * Get current pipeline analysis progress
 */
export const getPipelineStatus = () => apiFetch("/pipeline/status");
