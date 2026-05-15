// src/api/localServer.js
// Calls the Flask backend running on the therapist's local machine at port 5000

const BASE = "http://localhost:5000";

async function apiFetch(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

/** Ping the local server to check it's running */
export const checkServerHealth = () => apiFetch("/health");

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
