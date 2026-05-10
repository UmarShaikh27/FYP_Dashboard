// components/BackendStatus.jsx
// Floating indicator showing whether the local Flask backend is reachable.

import { useEffect, useState } from "react";
import {
  onBackendStatusChange,
  startHealthPolling,
  stopHealthPolling,
} from "../api/localServer";

export default function BackendStatus() {
  const [connected, setConnected] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    startHealthPolling(5000);
    const unsub = onBackendStatusChange(setConnected);
    return () => {
      unsub();
      stopHealthPolling();
    };
  }, []);

  // Don't render anything if the user dismissed the offline banner
  // and the backend is still offline (let them work in peace)
  if (dismissed && !connected) return null;
  // Reset dismissed state when backend comes back
  if (connected && dismissed) setDismissed(false);

  return (
    <div
      className={`backend-status ${connected ? "backend-status--online" : "backend-status--offline"}`}
    >
      <span className="backend-status__dot" />
      <span className="backend-status__label">
        {connected
          ? "Local Backend Connected"
          : "Local Backend Offline"}
      </span>
      {!connected && (
        <>
          <span className="backend-status__hint">
            Run <code>start.bat</code> on the therapy PC
          </span>
          <button
            className="backend-status__dismiss"
            onClick={() => setDismissed(true)}
            title="Dismiss"
          >
            ✕
          </button>
        </>
      )}
    </div>
  );
}
