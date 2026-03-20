"""
shoulder_origin.py  —  PhysioSync Motion Capture Script
=========================================================
Converted from shoulder_origin.ipynb.

Records Shoulder, Elbow, and Wrist 3D positions using:
  - Intel RealSense D435i (depth camera) for metric 3D coordinates
  - MediaPipe Pose Landmarker (Tasks API, VIDEO mode)

Output Excel columns:
  timestamp, Shoulder_x/y/z, Elbow_x/y/z, Wrist_x/y/z,
  upper_arm_length, forearm_length, total_arm_length,
  wrist_relative_x/y/z, wrist_normalized_x/y/z

The server.py launches this script as a subprocess and injects
configuration via environment variables so no interactive prompts
are needed during a session.

Environment variables (all optional, defaults shown):
  MOCAP_CAMERA       realsense / webcam       (default: realsense)
  MOCAP_ARM          right / left             (default: right)
  MOCAP_DURATION     float seconds            (default: 6.0)
  MOCAP_GRACE        float seconds            (default: 6.0)
  MOCAP_EXERCISE     string label             (default: exercise)
  MOCAP_TRAIL        string label             (default: trail_1)
  MOCAP_OUTPUT_DIR   path                     (default: ./output_excel)
  MOCAP_MODEL_PATH   path to .task file       (REQUIRED for RealSense mode)

Dependencies:
  pip install opencv-python mediapipe pyrealsense2 pandas openpyxl numpy matplotlib
"""

import cv2
import os
import sys
import time
from datetime import datetime
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')   # No GUI for the plot — server saves it to file
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import mediapipe as mp

try:
    import pyrealsense2 as rs
    REALSENSE_AVAILABLE = True
except ImportError:
    REALSENSE_AVAILABLE = False


class MotionCaptureApp:
    def __init__(self):
        # ── Read config from environment variables (injected by server.py) ──
        self.camera_source  = os.environ.get("MOCAP_CAMERA",    "realsense")
        self.selected_arm   = os.environ.get("MOCAP_ARM",       "right")
        self.duration       = float(os.environ.get("MOCAP_DURATION", "6.0"))
        self.grace_period   = float(os.environ.get("MOCAP_GRACE",    "6.0"))
        self.exercise_type  = os.environ.get("MOCAP_EXERCISE",  "exercise")
        self.trail          = os.environ.get("MOCAP_TRAIL",     "trail_1")
        self.output_dir     = os.environ.get("MOCAP_OUTPUT_DIR", os.path.join(os.path.dirname(__file__), "output_excel"))
        self.model_path     = os.environ.get("MOCAP_MODEL_PATH", "")

        # Hardware
        self.pipeline = None
        self.config   = None
        self.align    = None
        self.cap      = None

        # Data
        self.motion_data        = []
        self.recording_start_time = 0

        # Pose landmark indices (MediaPipe)
        self.POSE_LANDMARKS = {
            'RIGHT_SHOULDER': 12, 'RIGHT_ELBOW': 14, 'RIGHT_WRIST': 16,
            'LEFT_SHOULDER':  11, 'LEFT_ELBOW':  13, 'LEFT_WRIST':  15,
        }

        self.POSE_CONNECTIONS = [
            (11,12),(11,13),(13,15),(12,14),(14,16),
            (11,23),(12,24),(23,24),(23,25),(24,26),
            (25,27),(26,28),(27,29),(28,30),(29,31),(30,32),
        ]

        self.landmarker = None
        self._setup_pose_landmarker()

    # ── MediaPipe setup ───────────────────────────────────────────────────────

    def _setup_pose_landmarker(self):
        if not self.model_path or not os.path.exists(self.model_path):
            print(f"Model not found at: '{self.model_path}'")
            print("   Set MOCAP_MODEL_PATH env var to your pose_landmarker_lite.task path.")
            print("   Falling back to MediaPipe Solutions API (less accurate).")
            self.use_tasks_api = False
            self._mp_pose = mp.solutions.pose.Pose(
                model_complexity=1, smooth_landmarks=True
            )
            return

        self.use_tasks_api = True
        BaseOptions          = mp.tasks.BaseOptions
        PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
        VisionRunningMode    = mp.tasks.vision.RunningMode

        options = PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.model_path),
            running_mode=VisionRunningMode.VIDEO,
            min_pose_detection_confidence=0.7,
            min_pose_presence_confidence=0.7,
            min_tracking_confidence=0.7,
        )
        self.landmarker = mp.tasks.vision.PoseLandmarker.create_from_options(options)
        print("MediaPipe Pose Landmarker Initialized (Tasks API, VIDEO mode)")

    # ── Camera setup ──────────────────────────────────────────────────────────

    def _setup_realsense(self):
        if not REALSENSE_AVAILABLE:
            print("pyrealsense2 not installed.")
            return False
        try:
            self.pipeline = rs.pipeline()
            self.config   = rs.config()
            self.config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16,  30)
            self.config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
            self.align = rs.align(rs.stream.color)
            self.pipeline.start(self.config)
            print("RealSense D435i Connected.")
            return True
        except Exception as e:
            print(f"RealSense Error: {e}")
            return False

    def _setup_webcam(self):
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            print("Webcam not found.")
            return False
        print("Webcam Connected.")
        return True

    # ── Frame acquisition ─────────────────────────────────────────────────────

    def _get_frames(self):
        if self.camera_source == 'realsense':
            try:
                frames         = self.pipeline.wait_for_frames(timeout_ms=5000)
                aligned_frames = self.align.process(frames)
                color_frame    = aligned_frames.get_color_frame()
                depth_frame    = aligned_frames.get_depth_frame()
                if not color_frame or not depth_frame:
                    return None, None
                return np.asanyarray(color_frame.get_data()), depth_frame
            except:
                return None, None
        else:
            success, img = self.cap.read()
            return (img, None) if success else (None, None)

    # ── 3D deprojection ───────────────────────────────────────────────────────

    def _deproject(self, depth_frame, x, y):
        """Pixel (x,y) → metric 3D point via RealSense intrinsics."""
        if not depth_frame:
            return None
        w, h = depth_frame.get_width(), depth_frame.get_height()
        x, y = max(0, min(x, w-1)), max(0, min(y, h-1))
        dist  = depth_frame.get_distance(x, y)
        if dist <= 0:
            return None
        intr  = depth_frame.profile.as_video_stream_profile().intrinsics
        return rs.rs2_deproject_pixel_to_point(intr, [x, y], dist)

    # ── Landmark detection ────────────────────────────────────────────────────

    def _detect(self, image_rgb, timestamp_ms):
        """Returns list of landmarks or None."""
        if self.use_tasks_api:
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
            result = self.landmarker.detect_for_video(mp_img, timestamp_ms)
            if result and result.pose_landmarks:
                return result.pose_landmarks[0]
        else:
            result = self._mp_pose.process(image_rgb)
            if result.pose_landmarks:
                return result.pose_landmarks.landmark
        return None

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw_landmarks(self, image, landmarks):
        h, w, _ = image.shape
        for lm in landmarks:
            cv2.circle(image, (int(lm.x * w), int(lm.y * h)), 4, (0, 255, 0), -1)
        for start_i, end_i in self.POSE_CONNECTIONS:
            s, e = landmarks[start_i], landmarks[end_i]
            cv2.line(image,
                     (int(s.x * w), int(s.y * h)),
                     (int(e.x * w), int(e.y * h)),
                     (255, 255, 255), 2)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        # Initialize hardware
        if self.camera_source == 'realsense':
            if not self._setup_realsense():
                sys.exit(1)
        else:
            if not self._setup_webcam():
                sys.exit(1)

        # print("\n─────────────────────────────────")
        print(f"  Arm:      {self.selected_arm}")
        print(f"  Duration: {self.duration}s  |  Grace: {self.grace_period}s")
        print(f"  Exercise: {self.exercise_type}")
        print("  Controls: [SPACE] Start  |  [q] Quit")
        # print("─────────────────────────────────\n")

        # Joint indices for selected arm
        prefix = "RIGHT" if self.selected_arm == "right" else "LEFT"
        joint_indices = {
            'Shoulder': self.POSE_LANDMARKS[f'{prefix}_SHOULDER'],
            'Elbow':    self.POSE_LANDMARKS[f'{prefix}_ELBOW'],
            'Wrist':    self.POSE_LANDMARKS[f'{prefix}_WRIST'],
        }

        state      = "IDLE"
        grace_start = 0

        try:
            while True:
                image, depth_frame = self._get_frames()
                if image is None:
                    continue

                image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                image_rgb.flags.writeable = False
                timestamp_ms = int(time.time() * 1000)

                landmarks = self._detect(image_rgb, timestamp_ms)

                image_rgb.flags.writeable = True
                image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)

                if landmarks:
                    self._draw_landmarks(image_bgr, landmarks)
                    h, w, _ = image.shape

                    if state == "RECORDING":
                        row = {'timestamp': time.time() - self.recording_start_time}

                        for name, idx in joint_indices.items():
                            lm = landmarks[idx]
                            px, py = int(lm.x * w), int(lm.y * h)

                            p3d = None
                            if self.camera_source == 'realsense':
                                p3d = self._deproject(depth_frame, px, py)

                            if p3d:
                                row[f'{name}_x'] = p3d[0]
                                row[f'{name}_y'] = p3d[1]
                                row[f'{name}_z'] = p3d[2]
                            else:
                                # Fallback to normalized 2D (webcam mode)
                                row[f'{name}_x'] = lm.x
                                row[f'{name}_y'] = lm.y
                                row[f'{name}_z'] = lm.z

                        self.motion_data.append(row)

                        elapsed   = time.time() - self.recording_start_time
                        remaining = max(0, self.duration - elapsed)
                        cv2.circle(image_bgr, (30, 40), 12, (0, 0, 255), -1)
                        cv2.putText(image_bgr, f"REC: {remaining:.1f}s",
                                    (55, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

                        if elapsed >= self.duration:
                            state = "FINISHED"

                # UI overlays
                if state == "IDLE":
                    cv2.putText(image_bgr, "Press SPACE to Start", (40, 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

                elif state == "GRACE":
                    elapsed_grace    = time.time() - grace_start
                    remaining_grace  = max(0, self.grace_period - elapsed_grace)
                    cv2.putText(image_bgr, "GET READY", (200, 200),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
                    cv2.putText(image_bgr, f"{remaining_grace:.1f}", (280, 280),
                                cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 255, 255), 5)
                    if remaining_grace <= 0:
                        state = "RECORDING"
                        self.recording_start_time = time.time()
                        print("Recording started.")

                elif state == "FINISHED":
                    cv2.putText(image_bgr, "DONE! Saving...", (180, 240),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 0, 0), 3)
                    cv2.imshow("PhysioSync — Motion Capture", image_bgr)
                    cv2.waitKey(1000)
                    break

                cv2.imshow("PhysioSync — Motion Capture", image_bgr)

                key = cv2.waitKey(1)
                if key == ord('q'):
                    print("Quit requested.")
                    self.motion_data = []
                    break
                if key == ord(' ') and state == "IDLE":
                    state = "GRACE"
                    grace_start = time.time()

        finally:
            # ── Wrap every cleanup step individually so one failure
            #    doesn't prevent the others or corrupt the exit code ──────────
            try:
                if self.pipeline:
                    self.pipeline.stop()
            except Exception as e:
                print(f"[cleanup] pipeline.stop() warning: {e}")

            try:
                if self.cap:
                    self.cap.release()
            except Exception as e:
                print(f"[cleanup] cap.release() warning: {e}")

            try:
                cv2.destroyAllWindows()
            except Exception as e:
                print(f"[cleanup] destroyAllWindows() warning: {e}")

            try:
                if self.landmarker:
                    self.landmarker.close()
                elif hasattr(self, '_mp_pose'):
                    self._mp_pose.close()
            except Exception as e:
                print(f"[cleanup] landmarker.close() warning: {e}")

            # ── Save MUST happen last and must not be inside a bare except ───
            if self.motion_data:
                self._save(self.output_dir)
                # Explicit clean exit so server.py gets returncode 0
                sys.exit(0)
            else:
                print("No data collected.")
                sys.exit(1)

    # ── Save & normalize ──────────────────────────────────────────────────────

    def _save(self, output_dir):
        print("\nProcessing Data...")
        df = pd.DataFrame(self.motion_data)

        # ── Arm length scalars (averaged to reduce per-frame jitter) ──────────
        per_frame_upper = np.sqrt(
            (df['Elbow_x']   - df['Shoulder_x'])**2 +
            (df['Elbow_y']   - df['Shoulder_y'])**2 +
            (df['Elbow_z']   - df['Shoulder_z'])**2
        )
        per_frame_forearm = np.sqrt(
            (df['Wrist_x'] - df['Elbow_x'])**2 +
            (df['Wrist_y'] - df['Elbow_y'])**2 +
            (df['Wrist_z'] - df['Elbow_z'])**2
        )

        upper_arm_length = per_frame_upper.mean()
        forearm_length   = per_frame_forearm.mean()
        total_arm_length = upper_arm_length + forearm_length

        # Store as constant columns so scaling_template can read them later
        df['upper_arm_length'] = upper_arm_length
        df['forearm_length']   = forearm_length
        df['total_arm_length'] = total_arm_length

        # ── Wrist path relative to shoulder (shoulder = origin) ───────────────
        df['wrist_relative_x'] = df['Wrist_x'] - df['Shoulder_x']
        df['wrist_relative_y'] = df['Wrist_y'] - df['Shoulder_y']
        df['wrist_relative_z'] = df['Wrist_z'] - df['Shoulder_z']

        # ── Normalized wrist path (unit-less, body-agnostic) ──────────────────
        df['wrist_normalized_x'] = df['wrist_relative_x'] / total_arm_length
        df['wrist_normalized_y'] = df['wrist_relative_y'] / total_arm_length
        df['wrist_normalized_z'] = df['wrist_relative_z'] / total_arm_length

        # ── Save ──────────────────────────────────────────────────────────────
        os.makedirs(output_dir, exist_ok=True)
        # Add timestamp to filename for uniqueness (format: YYYYMMDD_HHMMSS_mmm)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]  # Last 3 digits = milliseconds
        # filename  = f"{self.selected_arm}_pose_{self.camera_source}_{self.trail}_{timestamp}.xlsx"
        filename  = f"pose_{self.camera_source}_{timestamp}.xlsx"
        full_path = os.path.join(output_dir, filename)
        df.to_excel(full_path, index=False)
        print(f"Excel Saved: {full_path}")
        return full_path


if __name__ == "__main__":
    app = MotionCaptureApp()
    app.run()
