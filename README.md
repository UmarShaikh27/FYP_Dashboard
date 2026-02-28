# PhysioSync — Setup Guide

## Folder Structure
```
physio-dashboard/
├── index.html
├── package.json
├── vite.config.js
└── src/
    ├── main.jsx              ← React entry point
    ├── App.jsx               ← Auth routing (therapist / patient)
    ├── index.css             ← Global styles
    ├── firebase/
    │   ├── config.js         ← 🔴 PUT YOUR FIREBASE KEYS HERE
    │   ├── auth.js           ← Login / logout helpers
    │   └── db.js             ← Firestore read/write helpers
    └── components/
        ├── Login.jsx             ← Shared login screen
        ├── TherapistDashboard.jsx← Therapist main view
        ├── ExerciseSession.jsx   ← Session form + Unity launcher
        ├── ProgressTable.jsx     ← Records table + XLSX export
        └── PatientDashboard.jsx  ← Patient progress view
```

---

## Step 1 — Install Node.js
Download from https://nodejs.org (LTS version)

## Step 2 — Install dependencies
```bash
cd physio-dashboard
npm install
```

## Step 3 — Set up Firebase (FREE)
1. Go to https://console.firebase.google.com
2. Click "Add project" → give it a name → Create
3. Click the `</>` Web icon to add a web app
4. Copy the config object and paste it into `src/firebase/config.js`
5. Go to **Authentication** → Get Started → Enable **Email/Password**
6. Go to **Firestore Database** → Create database → Start in **test mode**

## Step 4 — Add users to Firebase
Firestore needs a `users` collection. For each user add a document:

**Therapist document** (document ID = their Firebase Auth UID):
```json
{
  "name": "Sarah Ahmed",
  "email": "sarah@clinic.com",
  "role": "therapist"
}
```

**Patient document** (document ID = their Firebase Auth UID):
```json
{
  "name": "Ali Hassan",
  "email": "ali@gmail.com",
  "role": "patient"
}
```

To get the UID: Firebase Console → Authentication → Users → copy the User UID

## Step 5 — Run locally
```bash
npm run dev
```
Open http://localhost:5173

## Step 6 — Deploy for FREE on Vercel
1. Push your project to GitHub
2. Go to https://vercel.com → Import your repo
3. Click Deploy — done! Free HTTPS URL instantly.

---

## Unity Game Integration
The dashboard launches Unity via a custom URL scheme:
```
physio://start?exercise=Shoulder%20Rotation&reps=10&duration=15
```
In your Unity project, register the `physio://` protocol:
- **Windows**: Add a registry entry or use a manifest
- **Mac**: Add `CFBundleURLTypes` to Info.plist
- **WebGL**: Embed the Unity build in an iframe instead

---

## Firestore Rules (set before going live)
```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /users/{userId} {
      allow read: if request.auth.uid == userId;
    }
    match /sessions/{sessionId} {
      allow read: if request.auth != null &&
        (resource.data.patientId == request.auth.uid ||
         get(/databases/$(database)/documents/users/$(request.auth.uid)).data.role == 'therapist');
      allow write: if request.auth != null &&
        get(/databases/$(database)/documents/users/$(request.auth.uid)).data.role == 'therapist';
    }
  }
}
```
