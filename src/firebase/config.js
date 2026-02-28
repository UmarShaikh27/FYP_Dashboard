// firebase/config.js
// ─────────────────────────────────────────────
// STEP 1: Go to https://console.firebase.google.com
// STEP 2: Create a new project
// STEP 3: Add a Web App and copy your config below
// STEP 4: Enable Authentication → Email/Password
// STEP 5: Enable Firestore Database
// ─────────────────────────────────────────────

import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { getFirestore } from "firebase/firestore";

const firebaseConfig = {
  apiKey: "AIzaSyCsOLh8Su3V5vNTEV7Wati3OsWbD1o7VkM",
  authDomain: "gap-dashboard-abb76.firebaseapp.com",
  projectId: "gap-dashboard-abb76",
  storageBucket: "gap-dashboard-abb76.firebasestorage.app",
  messagingSenderId: "783215295825",
  appId: "1:783215295825:web:76c9e910dc13bb1d45e770"
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const db = getFirestore(app);
// export const analytics = getAnalytics(app);


