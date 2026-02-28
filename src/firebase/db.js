// firebase/db.js
import { db } from "./config";
import {
  collection,
  addDoc,
  getDocs,
  query,
  where,
  orderBy,
  serverTimestamp,
} from "firebase/firestore";

// Save a completed exercise session
// sessionData: { patientId, patientName, exerciseName, durationMinutes, repsCompleted, notes }
export const saveSession = async (sessionData) => {
  return await addDoc(collection(db, "sessions"), {
    ...sessionData,
    createdAt: serverTimestamp(),
  });
};

// Get all sessions for a specific patient
export const getPatientSessions = async (patientId) => {
  const q = query(
    collection(db, "sessions"),
    where("patientId", "==", patientId),
    orderBy("createdAt", "desc")
  );
  const snapshot = await getDocs(q);
  return snapshot.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
};

// Get all patients (for therapist view)
export const getAllPatients = async () => {
  const snapshot = await getDocs(
    query(collection(db, "users"), where("role", "==", "patient"))
  );
  return snapshot.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
};
