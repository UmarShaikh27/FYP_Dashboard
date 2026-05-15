"""
capture.py  –  Live-capture module for the unified physiotherapy pipeline.

Implements the MotionCaptureApp logic (originally from shoulder_origin.ipynb)
using MediaPipe Pose Landmarker for skeleton tracking and optionally an
Intel RealSense D435i for metric 3D coordinates.

Gesture Control (v3):
    In addition to keyboard controls, recording can be started/stopped
    via hand gestures detected by the GestureRecognizer module:
      - Left Open Palm (✋)  →  Hold for 3 s to start the exercise timer.
                              Recording auto-stops when the configured
                              duration expires.
      - Left Peace Sign (✌️)  →  Failsafe: hold to terminate early.

Usage (from main_pipeline.ipynb):
    from capture import run_capture
    raw_path = run_capture(patient_name="John",
                           arm="right",
                           duration=30,
                           grace_period=5,
                           exercise_type="eight_tracing",
                           output_dir="outputs/John/1",
                           trail=1)

Author:  Pipeline Builder
"""

import os
import sys
import time

import cv2
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

import mediapipe as mp

# ── Optional RealSense import ──────────────────────────────────────────────
try:
    import pyrealsense2 as rs
    _HAS_REALSENSE = True
except ImportError:
    _HAS_REALSENSE = False


# ═══════════════════════════════════════════════════════════════════════════
#  MotionCaptureApp
# ═══════════════════════════════════════════════════════════════════════════
class MotionCaptureApp:
    """Full motion-capture session using MediaPipe + optional RealSense."""

    # Pose landmark indices
    POSE_LANDMARKS = {
        'RIGHT_SHOULDER': 12,
        'RIGHT_ELBOW':    14,
        'RIGHT_WRIST':    16,
        'LEFT_SHOULDER':  11,
        'LEFT_ELBOW':     13,
        'LEFT_WRIST':     15,
    }

    # Pose connections for drawing
    POSE_CONNECTIONS = [
        (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
        (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
        (11, 23), (12, 24), (23, 24),
        (23, 25), (25, 27), (27, 29), (29, 31), (27, 31),
        (24, 26), (26, 28), (28, 30), (30, 32), (28, 32),
        (25, 26),
    ]

    def __init__(
        self,
        selected_arm: str = "auto",
        duration: float | None = None,
        grace_period: float = 5.0,
        exercise_type: str = "eight_tracing",
        output_dir: str = ".",
        session: int = 1,
        camera_source: str = "realsense",
        model_path: str | None = None,
        gesture_enabled: bool = True,
        gesture_hold_seconds: float = 3.0,
    ):
        # Configuration ---------------------------------------------------
        self.camera_source = camera_source
        self.selected_arm = selected_arm
        self.duration = float(duration) if duration is not None else None
        self.grace_period = float(grace_period)
        self.exercise_type = exercise_type
        self.output_dir = output_dir
        self.session = session
        self.gesture_enabled = gesture_enabled
        self.gesture_hold_seconds = gesture_hold_seconds

        # Model path – default: models/pose_landmarker_lite.task (relative to this file at root)
        if model_path is None:
            model_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "models", "pose_landmarker_lite.task",
            )
        self.model_path = os.path.abspath(model_path)

        # Initialize MediaPipe Pose
        self._setup_pose_landmarker()

        # ── Gesture Recognition ──────────────────────────────────────────
        self.gesture_recognizer = None
        if self.gesture_enabled:
            from gesture_recognizer import GestureRecognizer
            self.gesture_recognizer = GestureRecognizer(
                hold_seconds=self.gesture_hold_seconds,
                max_num_hands=2,
            )

        # Hardware objects
        self.pipeline = None
        self.config = None
        self.align = None
        self.cap = None

        # Data state
        self.motion_data: list[dict] = []
        self.recording_start_time = 0.0

    # ── MediaPipe setup ─────────────────────────────────────────────────
    def _setup_pose_landmarker(self):
        BaseOptions = mp.tasks.BaseOptions
        PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.model_path),
            running_mode=VisionRunningMode.VIDEO,
            min_pose_detection_confidence=0.7,
            min_pose_presence_confidence=0.7,
            min_tracking_confidence=0.7,
        )
        self.landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)
        print("✅ MediaPipe Pose Landmarker Initialized (SYNCHRONOUS MODE)")

    # ── Camera setup ────────────────────────────────────────────────────
    def _setup_realsense(self) -> bool:
        if not _HAS_REALSENSE:
            print("❌ pyrealsense2 is not installed. Falling back to webcam.")
            self.camera_source = "webcam"
            return self._setup_webcam()
        try:
            self.pipeline = rs.pipeline()
            self.config = rs.config()
            self.config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
            self.config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
            self.align = rs.align(rs.stream.color)
            self.pipeline.start(self.config)
            print("✅ RealSense D435i Connected.")
            return True
        except Exception as e:
            print(f"❌ RealSense Error: {e} - Falling back to webcam...")
            self.camera_source = "webcam"
            return self._setup_webcam()
    def _setup_webcam(self) -> bool:
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            print("❌ Webcam not found.")
            return False
        print("✅ Webcam Connected.")
        return True

    # ── Frame acquisition ───────────────────────────────────────────────
    def _get_frames(self):
        """Returns (color_image, depth_frame) or (color_image, None)."""
        if self.camera_source == "realsense":
            try:
                frames = self.pipeline.wait_for_frames(timeout_ms=5000)
                aligned = self.align.process(frames)
                color_frame = aligned.get_color_frame()
                depth_frame = aligned.get_depth_frame()
                if not color_frame or not depth_frame:
                    return None, None
                return np.asanyarray(color_frame.get_data()), depth_frame
            except Exception:
                return None, None
        else:
            success, img = self.cap.read()
            return (img, None) if success else (None, None)

    # ── 3-D deprojection (RealSense only) ───────────────────────────────
    @staticmethod
    def _deproject_to_metric(depth_frame, x, y):
        if depth_frame is None:
            return None
        w, h = depth_frame.get_width(), depth_frame.get_height()
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        dist = depth_frame.get_distance(x, y)
        if dist <= 0:
            return None
        intrinsics = depth_frame.profile.as_video_stream_profile().intrinsics
        return rs.rs2_deproject_pixel_to_point(intrinsics, [x, y], dist)

    # ── Drawing helpers ─────────────────────────────────────────────────
    def _draw_landmarks(self, image, landmarks):
        h, w, _ = image.shape
        for lm in landmarks:
            cv2.circle(image, (int(lm.x * w), int(lm.y * h)), 4, (0, 255, 0), -1)
        for a, b in self.POSE_CONNECTIONS:
            sx, sy = int(landmarks[a].x * w), int(landmarks[a].y * h)
            ex, ey = int(landmarks[b].x * w), int(landmarks[b].y * h)
            cv2.line(image, (sx, sy), (ex, ey), (255, 255, 255), 2)

    # ── Main capture loop ───────────────────────────────────────────────
    def run(self) -> tuple[str, str] | None:
        """
        Open the camera, wait for the user to press SPACE or show a
        START gesture (index finger up), record for *duration* seconds, then
        save and return the output path.

        During recording, a STOP gesture (peace sign) or 'q' key can
        end the session early.
        """
        if self.duration is None:
            while True:
                try:
                    d = input("Enter Capture Duration (seconds): ").strip()
                    if d:
                        self.duration = float(d)
                        break
                except ValueError:
                    pass

        # Initialize hardware
        if self.camera_source == "realsense":
            if not self._setup_realsense():
                return None
        else:
            if not self._setup_webcam():
                return None

        # Arm is always set from the dashboard — resolve joint indices immediately
        if self.selected_arm not in ("left", "right"):
            self.selected_arm = "right"  # safe default
        joint_indices = {
            "Shoulder": self.POSE_LANDMARKS[f"{self.selected_arm.upper()}_SHOULDER"],
            "Elbow":    self.POSE_LANDMARKS[f"{self.selected_arm.upper()}_ELBOW"],
            "Wrist":    self.POSE_LANDMARKS[f"{self.selected_arm.upper()}_WRIST"],
        }
        print(f"\n[Capture] Tracking arm: {self.selected_arm.upper()}")

        print("\n-------------------------------------------")
        print(" Controls:")
        if self.gesture_enabled:
            print(" [☝️ Index Pointing Up] : Hold to Start Exercise")
            print(" [✌️ Left Peace Sign]   : Hold → Stop Early (failsafe)")
        print(" [SPACE] : Start Countdown & Recording")
        print(" [q]     : Quit")
        print("-------------------------------------------\n")

        state = "IDLE"  # Arm is pre-selected from dashboard, skip ARM_SELECTION
        grace_start = 0.0

        try:
            while True:
                # 1 – Capture
                image, depth_frame = self._get_frames()
                if image is None:
                    continue

                # 2 – Prepare
                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                image_rgb.flags.writeable = False
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)

                # 3 – Pose Detection (synchronous VIDEO mode)
                ts_ms = int(time.time() * 1000)
                result = self.landmarker.detect_for_video(mp_image, ts_ms)

                # 4 – Display
                image_rgb.flags.writeable = True
                image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)

                # ── Gesture Detection ────────────────────────────────────
                gesture = None
                if self.gesture_enabled and self.gesture_recognizer is not None:
                    gesture = self.gesture_recognizer.detect(image_bgr)

                # 5 – Process landmarks
                if result and result.pose_landmarks:
                    landmarks = result.pose_landmarks[0]
                    self._draw_landmarks(image_bgr, landmarks)
                    h, w, _ = image.shape

                    # Recording logic
                    if state == "RECORDING":
                        row: dict = {"timestamp": time.time() - self.recording_start_time}
                        for name, idx in joint_indices.items():
                            lm = landmarks[idx]
                            px, py = int(lm.x * w), int(lm.y * h)

                            p3d = None
                            if self.camera_source == "realsense":
                                p3d = self._deproject_to_metric(depth_frame, px, py)

                            if p3d:
                                row[f"{name}_x"] = p3d[0]
                                row[f"{name}_y"] = p3d[1]
                                row[f"{name}_z"] = p3d[2]
                            else:
                                row[f"{name}_x"] = lm.x
                                row[f"{name}_y"] = lm.y
                                row[f"{name}_z"] = lm.z

                        self.motion_data.append(row)

                        elapsed = time.time() - self.recording_start_time
                        remaining = max(0, self.duration - elapsed)
                        cv2.circle(image_bgr, (30, 40), 12, (0, 0, 255), -1)
                        cv2.putText(image_bgr, f"REC: {remaining:.1f}s", (55, 50),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                        if elapsed >= self.duration:
                            state = "FINISHED"

                # ── Draw gesture feedback overlay ────────────────────────
                if self.gesture_enabled and self.gesture_recognizer is not None:
                    self.gesture_recognizer.draw_feedback(image_bgr)

                # ── Global STOP gesture check ────────────────────────────
                if gesture == "STOP":
                    if state == "RECORDING":
                        print("✌️ STOP (peace sign) detected — ending recording early.")
                        state = "FINISHED"
                    elif state in ["IDLE", "GRACE"]:
                        print("✌️ STOP (peace sign) detected — aborting capture session.")
                        self.motion_data = []
                        break
                        
                # 6 – UI state machine
                if state == "IDLE":
                    if self.gesture_enabled:
                        cv2.putText(image_bgr, "Hold INDEX POINTING UP (3s) to Start",
                                    (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
                    else:
                        cv2.putText(image_bgr, "Press SPACE to Start", (40, 50),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                    # ── START gesture in IDLE state ──────────────────────
                    if gesture == "START":
                        print("☝️ START gesture detected — beginning grace period.")
                        state = "GRACE"
                        grace_start = time.time()

                elif state == "GRACE":
                    remaining_grace = max(0, self.grace_period - (time.time() - grace_start))
                    cv2.putText(image_bgr, "GET READY", (200, 200),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
                    cv2.putText(image_bgr, f"{remaining_grace:.1f}", (280, 280),
                                cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 255, 255), 5)
                    if remaining_grace <= 0:
                        state = "RECORDING"
                        self.recording_start_time = time.time()
                elif state == "FINISHED":
                    cv2.putText(image_bgr, "DONE! Saving...", (180, 240),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 0, 0), 3)
                    cv2.imshow("3D Motion Capture", image_bgr)
                    cv2.waitKey(1000)
                    break

                cv2.imshow("3D Motion Capture", image_bgr)
                key = cv2.waitKey(1)
                if key == ord("q"):
                    print("Quit requested.")
                    self.motion_data = []
                    break
                if key == ord(" ") and state == "IDLE":
                    state = "GRACE"
                    grace_start = time.time()

        finally:
            if self.pipeline:
                self.pipeline.stop()
            if self.cap:
                self.cap.release()
            cv2.destroyAllWindows()
            self.landmarker.close()
            if self.gesture_recognizer is not None:
                self.gesture_recognizer.close()

        # Save
        if self.motion_data:
            return self._save_and_plot()
        else:
            print("⚠️ No data collected.")
            return None

    # ── Save to Excel & plot ────────────────────────────────────────────
    def _save_and_plot(self) -> str:
        print("\nProcessing Data...")
        df = pd.DataFrame(self.motion_data)

        # ── Segment lengths (averaged to reduce jitter) ─────────────────
        per_upper = np.sqrt(
            (df["Elbow_x"] - df["Shoulder_x"]) ** 2
            + (df["Elbow_y"] - df["Shoulder_y"]) ** 2
            + (df["Elbow_z"] - df["Shoulder_z"]) ** 2
        )
        per_forearm = np.sqrt(
            (df["Wrist_x"] - df["Elbow_x"]) ** 2
            + (df["Wrist_y"] - df["Elbow_y"]) ** 2
            + (df["Wrist_z"] - df["Elbow_z"]) ** 2
        )
        upper_arm_length = per_upper.mean()
        forearm_length = per_forearm.mean()
        total_arm_length = upper_arm_length + forearm_length

        df["upper_arm_length"] = upper_arm_length
        df["forearm_length"] = forearm_length
        df["total_arm_length"] = total_arm_length

        # ── Shoulder-relative ───────────────────────────────────────────
        df["wrist_relative_x"] = df["Wrist_x"] - df["Shoulder_x"]
        df["wrist_relative_y"] = df["Wrist_y"] - df["Shoulder_y"]
        df["wrist_relative_z"] = df["Wrist_z"] - df["Shoulder_z"]

        # ── Normalized ──────────────────────────────────────────────────
        df["wrist_normalized_x"] = df["wrist_relative_x"] / total_arm_length
        df["wrist_normalized_y"] = df["wrist_relative_y"] / total_arm_length
        df["wrist_normalized_z"] = df["wrist_relative_z"] / total_arm_length

        # ── Write Excel ─────────────────────────────────────────────────
        os.makedirs(self.output_dir, exist_ok=True)
        filename = (
            f"{self.selected_arm}_arm_motion_{self.exercise_type}"
            f"_{self.camera_source}_session_{self.session}.xlsx"
        )
        full_path = os.path.join(self.output_dir, filename)
        df.to_excel(full_path, index=False)
        print(f"✅ Excel Saved: {full_path}")

        # ── 3-D plot ────────────────────────────────────────────────────
        self._plot_3d(df)

        return (full_path, self.selected_arm)

    # ── 3-D trajectory plot ─────────────────────────────────────────────
    def _plot_3d(self, df: pd.DataFrame):
        fig = plt.figure(figsize=(10, 8))
        ax = fig.add_subplot(111, projection="3d")

        joints = ["Shoulder", "Elbow", "Wrist"]
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]
        has_data = False

        for j, c in zip(joints, colors):
            cx, cy, cz = f"{j}_x", f"{j}_y", f"{j}_z"
            if cx in df.columns:
                valid = df.dropna(subset=[cx, cy, cz])
                if not valid.empty:
                    has_data = True
                    ax.plot(valid[cx], valid[cy], valid[cz],
                            label=j, color=c, linewidth=2,
                            marker="s", markersize=3, markevery=5)
                    ax.scatter(valid[cx].iloc[0], valid[cy].iloc[0],
                               valid[cz].iloc[0], color=c, s=50, marker="o")

        import matplotlib
        if has_data:
            ax.set_xlabel("X Axis")
            ax.set_ylabel("Y Axis")
            ax.set_zlabel("Z Axis")
            ax.set_title(f"3D Trajectory of {self.selected_arm.title()} Arm Motion")
            ax.legend()
            # Only show interactive plot if a GUI backend is available
            if matplotlib.get_backend().lower() != 'agg':
                plt.show()
            plt.close(fig)
        else:
            print("⚠️ No valid data points found for plotting.")


# ═══════════════════════════════════════════════════════════════════════════
#  Pipeline entry-point
# ═══════════════════════════════════════════════════════════════════════════
def run_capture(
    patient_name: str,
    arm: str,
    duration: float | None,
    grace_period: int,
    exercise_type: str,
    output_dir: str,
    session: int = 1,
    gesture_enabled: bool = False,
    gesture_hold_seconds: float = 2.0,
    camera_source: str = "realsense",
) -> tuple[str, str]:
    """
    Run a live motion-capture session and save the raw Excel file.

    Parameters
    ----------
    patient_name : str        – Human-readable patient identifier.
    arm : str                 – "left" or "right" (set from dashboard, not gesture).
    duration : float | None   – Recording duration in seconds.
    grace_period : int        – Seconds of countdown before recording starts.
    exercise_type : str       – Exercise label (set from dashboard).
    output_dir : str          – Folder where the raw capture Excel will be saved.
    session : int             – Session number (appended to filename).
    gesture_enabled : bool    – If True, START/STOP via hand gestures; SPACE still works.
    gesture_hold_seconds : float – How long a gesture must be held to confirm.
    camera_source : str       – "realsense" (default) or "webcam".

    Returns
    -------
    tuple[str, str]  – (Absolute path to the saved raw Excel file, selected_arm)
    """
    print(f"\n[Capture] Patient: {patient_name} | Arm: {arm} | Exercise: {exercise_type}")
    print(f"[Capture] Duration: {duration}s | Grace: {grace_period}s | Camera: {camera_source}")
    if gesture_enabled:
        print(f"[Capture] Gesture control ENABLED — START = Index Up | STOP = Peace Sign")
    else:
        print(f"[Capture] Press SPACE in the camera window to start recording.")

    app = MotionCaptureApp(
        selected_arm=arm,
        duration=duration,
        grace_period=grace_period,
        exercise_type=exercise_type,
        output_dir=output_dir,
        session=session,
        camera_source=camera_source,
        gesture_enabled=gesture_enabled,
        gesture_hold_seconds=gesture_hold_seconds,
    )

    result = app.run()

    if result is None:
        raise RuntimeError(
            "Capture session did not produce a file. "
            "Make sure the camera is connected and the recording was completed (not quit with 'q')."
        )

    raw_path, selected_arm = result
    print(f"[Capture] Complete → {raw_path}")
    return raw_path, selected_arm
