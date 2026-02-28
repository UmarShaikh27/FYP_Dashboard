// firebase/auth.js
import { auth, db } from "./config";
import {
  signInWithEmailAndPassword,
  signOut,
  onAuthStateChanged,
} from "firebase/auth";
import { doc, getDoc } from "firebase/firestore";

// Login — returns user + their role from Firestore
export const loginUser = async (email, password) => {
  const userCredential = await signInWithEmailAndPassword(auth, email, password);
  const userDoc = await getDoc(doc(db, "users", userCredential.user.uid));
  if (!userDoc.exists()) throw new Error("User profile not found.");
  return { uid: userCredential.user.uid, ...userDoc.data() };
};

// Logout
export const logoutUser = () => signOut(auth);

// Listen to auth changes and resolve the role from Firestore
export const onAuthChange = (callback) => {
  return onAuthStateChanged(auth, async (firebaseUser) => {
    if (firebaseUser) {
      const userDoc = await getDoc(doc(db, "users", firebaseUser.uid));
      callback(userDoc.exists() ? { uid: firebaseUser.uid, ...userDoc.data() } : null);
    } else {
      callback(null);
    }
  });
};
