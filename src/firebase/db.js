// firebase/db.js
import { db } from "./config";
import {
  collection,
  addDoc,
  getDocs,
  deleteDoc,
  doc,
  query,
  where,
  orderBy,
  serverTimestamp,
} from "firebase/firestore";

// ── Session (basic) ───────────────────────────────────────────────────────────
export const saveSession = async (sessionData) => {
  return await addDoc(collection(db, "sessions"), {
    ...sessionData,
    createdAt: serverTimestamp(),
  });
};

export const deleteSession = async (sessionId) => {
  return await deleteDoc(doc(db, "sessions", sessionId));
};

// ── Analysis Result ───────────────────────────────────────────────────────────
/**
 * Save full DTW analysis result to Firestore (Multi-Attempt Format).
 * analysisData shape:
 * {
 *   // Existing fields
 *   patientId, patientName, therapistId,
 *   exerciseName, templateFile, recordingFile,
 *   
 *   // Legacy single-score (backward compatibility)
 *   score, global_rmse, axis_rmse,
 *   rom_ratio, rom_ratios, rom_axis_grades, avg_rom_grade,
 *   shape_grade, sparc, sparc_grades, report_text, plot_image_b64,
 *   
 *   // New multi-attempt fields
 *   exercise_type, num_attempts, per_attempt_scores,
 *   per_attempt_metrics, weighted_scores, global_score,
 *   weights_config, segmentation_params, session_summary,
 *   attempt_progression, session_plot_image_b64
 * }
 */
export const saveAnalysisResult = async (analysisData) => {
  return await addDoc(collection(db, "analysisResults"), {
    ...analysisData,
    createdAt: serverTimestamp(),
    modifiedAt: serverTimestamp(),
  });
};

export const deleteAnalysisResult = async (analysisId) => {
  return await deleteDoc(doc(db, "analysisResults", analysisId));
};

// ── Queries ───────────────────────────────────────────────────────────────────
export const getPatientSessions = async (patientId) => {
  const q = query(
    collection(db, "sessions"),
    where("patientId", "==", patientId),
    orderBy("createdAt", "desc")
  );
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
};

export const getPatientAnalyses = async (patientId) => {
  const q = query(
    collection(db, "analysisResults"),
    where("patientId", "==", patientId),
    orderBy("createdAt", "desc")
  );
  const snap = await getDocs(q);
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
};

export const getAllPatients = async () => {
  const snap = await getDocs(
    query(collection(db, "users"), where("role", "==", "patient"))
  );
  return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
};
