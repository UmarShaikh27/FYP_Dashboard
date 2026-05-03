"""
unity_bridge.py  —  Headless motion capture bridge for the Unity gamified session.
==================================================================================

Records Shoulder/Elbow/Wrist 3D positions using MediaPipe + RealSense WITHOUT
displaying a CV2 window.  The patient looks at the Unity game instead of a
camera preview.

Communication with Unity (UDP):
  Port 50238  Unity → Python   "START_EXERCISE" command
  Port 50239  Python → Unity   timer ticks, status, final scores

Flow:
  1. Camera opens silently.
  2. Sends {"type":"config",...} to Unity with session parameters.
  3. Waits indefinitely for Unity to send "START_EXERCISE" (patient presses
     the Start button inside the game).
  4. Records for `duration` seconds, sending {"type":"timer","remaining":X}
     every 0.5 s to Unity.
  5. Saves the raw .xlsx (same format as capture.py) to output_dir.
  6. Caller runs the full scoring pipeline, then calls bridge.send_scores().
"""

import json
import os
import socket
import threading
import time
from datetime import datetime

import cv2
import mediapipe as mp
import numpy as np
import pandas as pd

# ── Optional RealSense ─────────────────────────────────────────────────────────
try:
    import pyrealsense2 as rs
    _HAS_REALSENSE = True
except ImportError:
    _HAS_REALSENSE = False

# ── UDP ports (must match Unity's ScoreManager.cs) ─────────────────────────────
UNITY_HOST   = "localhost"
COMMAND_PORT = 50238   # Unity → Python
SCORE_PORT   = 50239   # Python → Unity
UNITY_LANDMARK_PORT = 50237 # Python → Unity (Live avatar motion)

# ── Pose landmark indices ──────────────────────────────────────────────────────
_POSE_LANDMARKS = {
    "RIGHT_SHOULDER": 12, "RIGHT_ELBOW": 14, "RIGHT_WRIST": 16,
    "LEFT_SHOULDER":  11, "LEFT_ELBOW":  13, "LEFT_WRIST":  15,
}


class UnityBridgeCapture:
    """
    Headless motion capture session for the Unity gamified pipeline.

    Usage:
        bridge = UnityBridgeCapture(arm="right", duration=20, ...)
        xlsx_path = bridge.run()        # blocks until done
        bridge.send_scores(result_dict) # after server.py has scored
        bridge.close()                  # release UDP sockets
    """

    def __init__(
        self,
        arm: str = "right",
        duration: float = 20.0,
        exercise_type: str = "eight_tracing",
        output_dir: str = "output_excel",
        camera_source: str = "realsense",
        model_path: str | None = None,
    ):
        self.arm = arm if arm in ("left", "right") else "right"
        self.duration = float(duration)
        self.exercise_type = exercise_type
        self.output_dir = output_dir
        self.camera_source = camera_source

        # Model path — default: models/pose_landmarker_lite.task at root
        if model_path is None:
            model_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "models", "pose_landmarker_lite.task",
            )
        self.model_path = os.path.abspath(model_path)

        # Joint indices for selected arm
        self.joint_indices = {
            "Shoulder": _POSE_LANDMARKS[f"{self.arm.upper()}_SHOULDER"],
            "Elbow":    _POSE_LANDMARKS[f"{self.arm.upper()}_ELBOW"],
            "Wrist":    _POSE_LANDMARKS[f"{self.arm.upper()}_WRIST"],
        }

        # Camera objects (set up in run())
        self._rs_pipeline = None
        self._rs_align    = None
        self._cap         = None
        self._landmarker  = None

        # UDP sockets
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._cmd_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._lm_sock   = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._cmd_sock.settimeout(0.5)

        # State
        self._start_event  = threading.Event()
        self._stop_event   = threading.Event()
        self._motion_data: list[dict] = []

    # ── UDP helpers ────────────────────────────────────────────────────────────

    def _send(self, payload: dict):
        """Send a JSON payload to Unity on SCORE_PORT."""
        try:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self._send_sock.sendto(data, (UNITY_HOST, SCORE_PORT))
        except Exception as e:
            print(f"[Bridge] UDP send error: {e}")

    def _send_landmarks(self, pose_landmarks, depth_frame, img_w, img_h):
        """Send real-time 3D pose landmarks to animate the Unity avatar."""
        landmarks_out = []
        for idx, lm in enumerate(pose_landmarks):
            px = int(lm.x * img_w)
            py = int(lm.y * img_h)
            x, y, z = float(lm.x), float(lm.y), float(lm.z)
            if self.camera_source == "realsense" and depth_frame is not None:
                p3d = self._deproject(depth_frame, px, py)
                if p3d is not None:
                    x, y, z = p3d[0], p3d[1], p3d[2]
            
            landmarks_out.append({"id": idx, "x": x, "y": y, "z": z, "v": round(float(lm.visibility), 2)})
            
        payload = {
            "landmarks": landmarks_out,
            "left_hand": [],
            "right_hand": []
        }
        try:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self._lm_sock.sendto(data, (UNITY_HOST, UNITY_LANDMARK_PORT))
        except Exception:
            pass

    # ── Camera setup ───────────────────────────────────────────────────────────

    def _setup_camera(self) -> bool:
        if self.camera_source == "realsense" and _HAS_REALSENSE:
            try:
                self._rs_pipeline = rs.pipeline()
                cfg = rs.config()
                cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
                cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
                self._rs_align = rs.align(rs.stream.color)
                self._rs_pipeline.start(cfg)
                print("[Bridge] RealSense D435i connected (headless mode)")
                return True
            except Exception as e:
                print(f"[Bridge] RealSense failed ({e}), falling back to webcam")
                self._rs_pipeline = None

        # Webcam fallback
        self._cap = cv2.VideoCapture(0)
        self.camera_source = "webcam"
        if self._cap.isOpened():
            print("[Bridge] Webcam connected (headless mode)")
            return True
        print("[Bridge] ERROR: No camera available")
        return False

    def _get_frames(self):
        """Returns (color_bgr_ndarray, depth_frame_or_None)."""
        if self._rs_pipeline is not None:
            try:
                frames   = self._rs_pipeline.wait_for_frames(timeout_ms=5000)
                aligned  = self._rs_align.process(frames)
                color_f  = aligned.get_color_frame()
                depth_f  = aligned.get_depth_frame()
                if not color_f or not depth_f:
                    return None, None
                return np.asanyarray(color_f.get_data()), depth_f
            except Exception:
                return None, None
        else:
            ok, img = self._cap.read()
            return (img, None) if ok else (None, None)

    @staticmethod
    def _deproject(depth_frame, x: int, y: int):
        if depth_frame is None:
            return None
        w = depth_frame.get_width()
        h = depth_frame.get_height()
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        dist = depth_frame.get_distance(x, y)
        if dist <= 0:
            return None
        intr = depth_frame.profile.as_video_stream_profile().intrinsics
        return rs.rs2_deproject_pixel_to_point(intr, [x, y], dist)

    # ── MediaPipe setup ────────────────────────────────────────────────────────

    def _setup_mediapipe(self):
        BaseOptions          = mp.tasks.BaseOptions
        PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
        VisionRunningMode    = mp.tasks.vision.RunningMode

        opts = PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.model_path),
            running_mode=VisionRunningMode.VIDEO,
            min_pose_detection_confidence=0.7,
            min_pose_presence_confidence=0.7,
            min_tracking_confidence=0.7,
        )
        self._landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(opts)
        print("[Bridge] MediaPipe Pose Landmarker initialized")

    # ── Command listener (background thread) ───────────────────────────────────

    def _listen_for_start(self):
        """Background thread: waits for START_EXERCISE UDP from Unity."""
        try:
            self._cmd_sock.bind(("0.0.0.0", COMMAND_PORT))
            print(f"[Bridge] Listening for START_EXERCISE on port {COMMAND_PORT}…")
            while not self._start_event.is_set() and not self._stop_event.is_set():
                try:
                    data, _ = self._cmd_sock.recvfrom(1024)
                    msg = data.decode("utf-8").strip()
                    print(f"[Bridge] Received command: '{msg}'")
                    if msg == "START_EXERCISE":
                        self._start_event.set()
                except socket.timeout:
                    continue
        except OSError as e:
            # Port already in use — emit a warning and don't crash
            print(f"[Bridge] WARNING: Could not bind command port {COMMAND_PORT}: {e}")
        except Exception as e:
            print(f"[Bridge] Command listener error: {e}")

    # ── Main capture loop ──────────────────────────────────────────────────────

    def run(self) -> str:
        """
        Full gamified capture session (blocking).

        Returns the absolute path to the saved .xlsx file.
        Raises RuntimeError on camera or data failure.
        """
        self._setup_mediapipe()
        if not self._setup_camera():
            raise RuntimeError("No camera available for gamified session")

        # Tell Unity the session config so it can display exercise name + duration
        self._send({
            "type":     "config",
            "duration": self.duration,
            "arm":      self.arm,
            "exercise": self.exercise_type,
        })
        self._send({"type": "status", "status": "waiting"})

        # Start the command listener
        listener = threading.Thread(target=self._listen_for_start, daemon=True)
        listener.start()

        print("[Bridge] Camera ready. Waiting for Unity 'Start Exercise' button…")

        # Warm up camera (discard first few frames to let exposure stabilise)
        for _ in range(10):
            self._get_frames()
            time.sleep(0.02)

        GRACE_PERIOD = 3.0
        grace_started = False
        grace_start_time = 0.0
        recording_started = False
        recording_start_time = 0.0
        last_timer_tick = -1.0
        self._motion_data = []

        while not self._stop_event.is_set():
            image, depth = self._get_frames()
            if image is None:
                continue

            h, w, _ = image.shape
            
            # Pose detection
            img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            img_rgb.flags.writeable = False
            mp_img  = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
            ts_ms   = int(time.time() * 1000)
            if ts_ms <= getattr(self, '_last_ts', 0):
                ts_ms = self._last_ts + 1
            self._last_ts = ts_ms
            
            result  = self._landmarker.detect_for_video(mp_img, ts_ms)

            # Draw status on the image
            status_text = "WAITING FOR UNITY START"
            if grace_started and not recording_started:
                remaining_grace = max(0.0, GRACE_PERIOD - (time.time() - grace_start_time))
                status_text = f"STARTING IN: {remaining_grace:.1f}s"
            elif recording_started:
                elapsed = time.time() - recording_start_time
                remaining = max(0.0, self.duration - elapsed)
                status_text = f"RECORDING: {remaining:.1f}s"

            cv2.putText(image, status_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)

            if result and result.pose_landmarks:
                cv2.putText(image, "TRACKING POSE", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
                # Draw simple landmarks
                for lm in result.pose_landmarks[0]:
                    cv2.circle(image, (int(lm.x * w), int(lm.y * h)), 4, (0, 255, 0), -1)
                self._send_landmarks(result.pose_landmarks[0], depth, w, h)
            else:
                cv2.putText(image, "NO POSE DETECTED - PLEASE STAND IN FRAME", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            # Show the camera feed for debugging
            cv2.imshow("Unity Bridge - Camera Feed", image)
            if cv2.waitKey(1) == ord('q'):
                break

            # 2. State machine
            if not grace_started:
                # Waiting for user to press Start Exercise

                # Continuously send config every 1s so Unity doesn't miss it if it boots slowly
                if time.time() - getattr(self, '_last_config_time', 0) > 1.0:
                    self._send({
                        "type":     "config",
                        "duration": self.duration,
                        "arm":      self.arm,
                        "exercise": self.exercise_type,
                    })
                    self._send({"type": "status", "status": "waiting"})
                    self._last_config_time = time.time()

                if self._start_event.is_set():
                    grace_started = True
                    grace_start_time = time.time()
                    print(f"[Bridge] Start pressed. Grace period ({GRACE_PERIOD}s) started.")
            
            elif not recording_started:
                # In Grace period (countdown)
                elapsed_grace = time.time() - grace_start_time
                remaining_grace = max(0.0, GRACE_PERIOD - elapsed_grace)

                if elapsed_grace - last_timer_tick >= 0.5 or last_timer_tick < 0:
                    self._send({"type": "status", "status": "recording"}) # Force Unity into recording state for the UI
                    self._send({
                        "type": "timer",
                        "remaining": round(remaining_grace, 1),
                        "elapsed": 0.0
                    })
                    last_timer_tick = elapsed_grace

                if elapsed_grace >= GRACE_PERIOD:
                    recording_started = True
                    recording_start_time = time.time()
                    last_timer_tick = -1.0
                    self._send({"type": "status", "status": "recording"})
                    print(f"[Bridge] Recording started for {self.duration:.1f}s")
            
            else:
                # Recording in progress
                elapsed = time.time() - recording_start_time
                remaining = max(0.0, self.duration - elapsed)

                if elapsed - last_timer_tick >= 0.5:
                    self._send({"type": "status", "status": "recording"}) # Ensure it stays in recording
                    self._send({
                        "type":      "timer",
                        "remaining": round(remaining, 1),
                        "elapsed":   round(elapsed, 1),
                    })
                    last_timer_tick = elapsed

                # Record data
                if result and result.pose_landmarks:
                    lms = result.pose_landmarks[0]
                    row = {"timestamp": elapsed}
                    for name, idx in self.joint_indices.items():
                        lm = lms[idx]
                        px = int(lm.x * w)
                        py = int(lm.y * h)
                        p3d = None
                        if self.camera_source == "realsense" and depth is not None:
                            p3d = self._deproject(depth, px, py)

                        if p3d:
                            row[f"{name}_x"] = p3d[0]
                            row[f"{name}_y"] = p3d[1]
                            row[f"{name}_z"] = p3d[2]
                        else:
                            row[f"{name}_x"] = float(lm.x)
                            row[f"{name}_y"] = float(lm.y)
                            row[f"{name}_z"] = float(lm.z)

                    self._motion_data.append(row)

                if elapsed >= self.duration:
                    break

        # ── Save ──────────────────────────────────────────────────────────────
        print(f"[Bridge] Recording complete — {len(self._motion_data)} frames captured")
        self._send({"type": "status", "status": "scoring"})
        self._cleanup_camera()

        if not self._motion_data:
            raise RuntimeError("No motion data captured during gamified session")

        return self._save_xlsx()

    # ── Score sender ───────────────────────────────────────────────────────────

    def send_done(self):
        """Notify Unity the session is finished so it can display 'Session Complete' and auto-close."""
        self._send({"type": "status", "status": "done"})
        print("[Bridge] Sent 'done' status to Unity — game will auto-close.")

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def _cleanup_camera(self):
        """Release camera resources only."""
        try:
            self._landmarker.close()
        except Exception:
            pass
        if self._rs_pipeline is not None:
            try:
                self._rs_pipeline.stop()
            except Exception:
                pass
            self._rs_pipeline = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def stop(self):
        """Signal the capture loop to stop early."""
        self._stop_event.set()

    def close(self):
        """Release all resources including UDP sockets."""
        self.stop()
        self._cleanup_camera()
        for sock in (self._cmd_sock, self._send_sock, self._lm_sock):
            try:
                sock.close()
            except Exception:
                pass

    # ── xlsx save (mirrors capture.py's _save_and_plot) ───────────────────────

    def _save_xlsx(self) -> str:
        df = pd.DataFrame(self._motion_data)

        # Arm-length normalisation columns (required by normalize.py)
        per_upper   = np.sqrt((df["Elbow_x"] - df["Shoulder_x"]) ** 2
                             + (df["Elbow_y"] - df["Shoulder_y"]) ** 2
                             + (df["Elbow_z"] - df["Shoulder_z"]) ** 2)
        per_forearm = np.sqrt((df["Wrist_x"] - df["Elbow_x"]) ** 2
                             + (df["Wrist_y"] - df["Elbow_y"]) ** 2
                             + (df["Wrist_z"] - df["Elbow_z"]) ** 2)

        upper_arm_length = per_upper.mean()
        forearm_length   = per_forearm.mean()
        total_arm_length = upper_arm_length + forearm_length

        df["upper_arm_length"] = upper_arm_length
        df["forearm_length"]   = forearm_length
        df["total_arm_length"] = total_arm_length

        df["wrist_relative_x"] = df["Wrist_x"] - df["Shoulder_x"]
        df["wrist_relative_y"] = df["Wrist_y"] - df["Shoulder_y"]
        df["wrist_relative_z"] = df["Wrist_z"] - df["Shoulder_z"]

        df["wrist_normalized_x"] = df["wrist_relative_x"] / total_arm_length
        df["wrist_normalized_y"] = df["wrist_relative_y"] / total_arm_length
        df["wrist_normalized_z"] = df["wrist_relative_z"] / total_arm_length

        os.makedirs(self.output_dir, exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.arm}_arm_gamified_{self.exercise_type}_{ts}.xlsx"
        path     = os.path.join(self.output_dir, filename)
        df.to_excel(path, index=False)
        print(f"[Bridge] Saved: {path}")
        return path
