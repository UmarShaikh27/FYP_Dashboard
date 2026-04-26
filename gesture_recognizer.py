"""
gesture_recognizer.py  –  Hand gesture recognition for exercise start/stop control.

Uses MediaPipe Hand Landmarker (Tasks API) to detect hand gestures from
camera frames in real-time.  This matches the same API style used in
capture.py for Pose Landmarker.

Designed to be extensible — new custom gestures can be added by defining
finger-state patterns or overriding the detection logic.

Built-in gestures (LEFT HAND only):
    START  →  Open Palm (all 5 fingers extended, LEFT hand, hold 3 s)
              Once confirmed the exercise timer starts automatically
              and recording stops when the configured duration expires.
    STOP   →  Peace Sign (index + middle fingers extended, LEFT hand)
              Failsafe to terminate the session early if needed.

Usage (standalone):
    from gesture_recognizer import GestureRecognizer

    gr = GestureRecognizer(hold_seconds=3.0)
    gesture = gr.detect(frame)   # returns "START", "STOP", or None
    gr.draw_feedback(frame)      # draws landmarks + status on frame
    gr.close()                   # release resources

Usage (with capture.py):
    Integrated automatically when gesture_enabled=True in MotionCaptureApp.

Author: Pipeline Builder
"""

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

import mediapipe as mp


# ═══════════════════════════════════════════════════════════════════════════
#  Gesture Definitions
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class GestureDefinition:
    """
    Defines a gesture as a pattern of finger states.

    Attributes
    ----------
    name : str
        Human-readable gesture name (e.g., "START", "STOP").
    display_label : str
        Label shown on the video feed (e.g., "Open Palm").
    finger_states : dict
        Required state for each finger. Keys are finger names
        ("THUMB", "INDEX", "MIDDLE", "RING", "PINKY").
        Values are True (extended) or False (curled).
        Missing keys = don't care (any state accepted).
    min_fingers_matching : int | None
        If set, overrides exact matching — at least this many fingers
        must match the pattern. Useful for approximate gestures.
    custom_detector : Callable | None
        If provided, this function is called instead of finger-state
        matching. Signature: (hand_landmarks, handedness) -> bool.
        Use for complex gestures that can't be expressed as finger states.
    color : tuple
        BGR color for drawing feedback.
    """
    name: str
    display_label: str
    finger_states: Dict[str, bool] = field(default_factory=dict)
    min_fingers_matching: Optional[int] = None
    custom_detector: Optional[Callable] = None
    color: Tuple[int, int, int] = (0, 255, 0)


# ── Left-hand-only custom detectors ─────────────────────────────────────
# NOTE: MediaPipe reports handedness as it appears in the (mirrored) webcam
# image.  The user's *actual* left hand appears on the right side of the
# image, so MediaPipe labels it "Right".  We therefore check for "Right"
# to match the user's real left hand.

def _is_pointing_up(landmarks, handedness: str) -> bool:
    """Return True only when the user shows a Pointing Up gesture
    (Index extended, Middle/Ring/Pinky curled). Thumb is ignored.
    (Handedness check removed to prevent MediaPipe flickering issues)"""
    states = get_finger_states(landmarks, handedness)
    return (
        states["INDEX"]
        and not states["MIDDLE"]
        and not states["RING"]
        and not states["PINKY"]
    )


def _is_left_hand_peace(landmarks, handedness: str) -> bool:
    """Return True only when the user shows a peace / V sign
    (index + middle extended, ring + pinky curled).  Thumb is ignored
    because it varies naturally across patients.
    (Handedness check removed to prevent MediaPipe flickering issues)"""
    states = get_finger_states(landmarks, handedness)
    return (
        states["INDEX"]
        and states["MIDDLE"]
        and not states["RING"]
        and not states["PINKY"]
    )


def _is_spiderman(landmarks, handedness: str) -> bool:
    """Thumb, Index, Pinky extended. Middle and Ring curled."""
    states = get_finger_states(landmarks, handedness)
    return (states["THUMB"] and states["INDEX"] and states["PINKY"]
            and not states["MIDDLE"] and not states["RING"])

def _is_shaka(landmarks, handedness: str) -> bool:
    """Thumb and Pinky extended. Index, Middle, Ring curled."""
    states = get_finger_states(landmarks, handedness)
    return (states["THUMB"] and states["PINKY"]
            and not states["INDEX"] and not states["MIDDLE"] and not states["RING"])

def _is_pinky_promise(landmarks, handedness: str) -> bool:
    """Pinky extended. All others curled."""
    states = get_finger_states(landmarks, handedness)
    return (states["PINKY"]
            and not states["THUMB"] and not states["INDEX"] 
            and not states["MIDDLE"] and not states["RING"])


# ── Built-in gesture definitions ────────────────────────────────────────
GESTURE_START = GestureDefinition(
    name="START",
    display_label="Pointing Up - START",
    custom_detector=_is_pointing_up,
    color=(0, 255, 0),      # green
)

GESTURE_STOP = GestureDefinition(
    name="STOP",
    display_label="Left Peace Sign - STOP",
    custom_detector=_is_left_hand_peace,
    color=(0, 0, 255),      # red
)

GESTURE_SPIDERMAN = GestureDefinition(
    name="EIGHT_TRACING",
    display_label="Spiderman -> 8 Tracing",
    custom_detector=_is_spiderman,
    color=(255, 0, 0),      # blue
)

GESTURE_SHAKA = GestureDefinition(
    name="CIRCUMDUCTION",
    display_label="Shaka -> Circumduction",
    custom_detector=_is_shaka,
    color=(0, 255, 255),    # yellow
)

GESTURE_PINKY = GestureDefinition(
    name="FLEXION",
    display_label="Pinky -> Flexion 2kg",
    custom_detector=_is_pinky_promise,
    color=(255, 0, 255),    # magenta
)


# ═══════════════════════════════════════════════════════════════════════════
#  Finger-State Analysis
# ═══════════════════════════════════════════════════════════════════════════

# MediaPipe hand landmark indices (same for Tasks API and Solutions API)
_FINGER_TIP_IDS = {
    "THUMB":  4,
    "INDEX":  8,
    "MIDDLE": 12,
    "RING":   16,
    "PINKY":  20,
}

_FINGER_PIP_IDS = {
    "THUMB":  2,     # For thumb we use MCP (2) as the proxy joint
    "INDEX":  6,
    "MIDDLE": 10,
    "RING":   14,
    "PINKY":  18,
}

WRIST_ID = 0


def _is_finger_extended(landmarks, finger_name: str, handedness: str = "Right") -> bool:
    """
    Determine if a finger is extended based on landmark positions.

    For the thumb:  tip.x vs IP.x relative to handedness.
    For others:     tip.y < PIP.y  (y increases downward in image coords).
    """
    tip = landmarks[_FINGER_TIP_IDS[finger_name]]
    pip = landmarks[_FINGER_PIP_IDS[finger_name]]

    if finger_name == "THUMB":
        # Thumb is extended when tip is further from the palm center
        # than the IP joint. Use x-axis comparison adjusted for handedness.
        if handedness == "Right":
            return tip.x < pip.x    # right hand: thumb extends left
        else:
            return tip.x > pip.x    # left hand: thumb extends right
    else:
        # Other fingers: tip above PIP = extended (y is inverted in image)
        return tip.y < pip.y


def get_finger_states(landmarks, handedness: str = "Right") -> Dict[str, bool]:
    """Return a dict mapping each finger name to True (extended) or False (curled)."""
    return {
        name: _is_finger_extended(landmarks, name, handedness)
        for name in _FINGER_TIP_IDS
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Hand Landmark Drawing (manual, matching capture.py style)
# ═══════════════════════════════════════════════════════════════════════════

# MediaPipe hand connections (pairs of landmark indices)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # index
    (0, 9), (9, 10), (10, 11), (11, 12),   # middle
    (0, 13), (13, 14), (14, 15), (15, 16), # ring
    (0, 17), (17, 18), (18, 19), (19, 20), # pinky
    (5, 9), (9, 13), (13, 17),              # palm
]


def _draw_hand_landmarks(image, landmarks, color=(0, 255, 0)):
    """Draw hand landmarks and connections on the image."""
    h, w, _ = image.shape
    points = []
    for lm in landmarks:
        px, py = int(lm.x * w), int(lm.y * h)
        points.append((px, py))
        cv2.circle(image, (px, py), 4, color, -1)

    for a, b in HAND_CONNECTIONS:
        if a < len(points) and b < len(points):
            cv2.line(image, points[a], points[b], (255, 255, 255), 2)


# ═══════════════════════════════════════════════════════════════════════════
#  GestureRecognizer
# ═══════════════════════════════════════════════════════════════════════════

class GestureRecognizer:
    """
    Real-time hand gesture recognizer using MediaPipe Hand Landmarker
    Tasks API (matches the pattern used in capture.py for Pose).

    Parameters
    ----------
    hold_seconds : float
        Duration (in seconds) a gesture must be held continuously
        to trigger confirmation. Prevents accidental triggers.
    max_num_hands : int
        Maximum number of hands to detect simultaneously.
    min_detection_confidence : float
        MediaPipe detection confidence threshold.
    min_tracking_confidence : float
        MediaPipe tracking confidence threshold.
    gestures : list[GestureDefinition] | None
        List of gestures to detect. If None, uses built-in START/STOP.
    model_path : str | None
        Path to the hand_landmarker.task model file.
        If None, defaults to ../../models/pose_landmarker_lite.task.
    """

    def __init__(
        self,
        hold_seconds: float = 3.0,
        max_num_hands: int = 2,
        min_detection_confidence: float = 0.7,
        min_tracking_confidence: float = 0.7,
        gestures: Optional[List[GestureDefinition]] = None,
        model_path: Optional[str] = None,
    ):
        self.hold_seconds = hold_seconds
        self.max_num_hands = max_num_hands

        # Gesture registry
        if gestures is None:
            self._gestures: List[GestureDefinition] = [GESTURE_START, GESTURE_STOP]
        else:
            self._gestures = list(gestures)

        # ── Model path ──────────────────────────────────────────────────
        if model_path is None:
            model_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "models", "hand_landmarker.task",
            )
        self._model_path = os.path.abspath(model_path)

        # ── MediaPipe Hand Landmarker (Tasks API) ───────────────────────
        self._setup_hand_landmarker(min_detection_confidence, min_tracking_confidence)

        # Hold tracking state: {gesture_name: first_detected_timestamp}
        self._hold_start: Dict[str, float] = {}
        self._confirmed_gesture: Optional[str] = None

        # Drawing state (updated each detect() call)
        self._last_hand_landmarks: list = []
        self._last_handedness: list = []
        self._last_detected_name: Optional[str] = None
        self._last_hold_progress: float = 0.0

    # ── MediaPipe setup (Tasks API) ─────────────────────────────────────
    def _setup_hand_landmarker(self, det_conf: float, track_conf: float):
        """Initialize MediaPipe Hand Landmarker using the Tasks API."""
        BaseOptions = mp.tasks.BaseOptions
        HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self._model_path),
            running_mode=VisionRunningMode.VIDEO,
            num_hands=self.max_num_hands,
            min_hand_detection_confidence=det_conf,
            min_hand_presence_confidence=det_conf,
            min_tracking_confidence=track_conf,
        )
        self._landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
        print("✅ MediaPipe Hand Landmarker Initialized (Gesture Recognition)")

    # ── Public API ──────────────────────────────────────────────────────

    def register_gesture(self, gesture: GestureDefinition) -> None:
        """
        Register a new custom gesture definition at runtime.

        This allows adding gestures beyond the built-in START/STOP.
        """
        # Replace if same name exists
        self._gestures = [g for g in self._gestures if g.name != gesture.name]
        self._gestures.append(gesture)
        print(f"[GestureRecognizer] Registered gesture: {gesture.name} ({gesture.display_label})")

    def detect(self, frame_bgr: np.ndarray) -> Optional[str]:
        """
        Process a camera frame and return a confirmed gesture name, or None.

        Parameters
        ----------
        frame_bgr : np.ndarray
            BGR image from OpenCV (camera feed).

        Returns
        -------
        str | None
            Confirmed gesture name ("START", "STOP", etc.) if the gesture
            was held for the required duration, otherwise None.
        """
        # Convert to RGB for MediaPipe
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frame_rgb.flags.writeable = False
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)

        # Run hand landmarker (VIDEO mode, needs monotonic timestamp)
        ts_ms = int(time.time() * 1000)
        result = self._landmarker.detect_for_video(mp_image, ts_ms)

        # Cache for drawing
        self._last_hand_landmarks = []
        self._last_handedness = []

        # Reset confirmed gesture each frame
        self._confirmed_gesture = None

        # If no hands detected, reset all holds
        if not result.hand_landmarks:
            self._hold_start.clear()
            self._last_detected_name = None
            self._last_hold_progress = 0.0
            return None

        # Store for drawing
        self._last_hand_landmarks = result.hand_landmarks
        self._last_handedness = result.handedness if result.handedness else []

        # Check each detected hand for gestures
        now = time.time()
        detected_gestures_this_frame: set = set()

        for hand_idx, hand_landmarks in enumerate(result.hand_landmarks):
            # Determine handedness
            handedness_label = "Right"
            if result.handedness and hand_idx < len(result.handedness):
                handedness_label = result.handedness[hand_idx][0].category_name

            # Get finger states for this hand
            finger_states = get_finger_states(hand_landmarks, handedness_label)

            # Check each registered gesture
            for gesture_def in self._gestures:
                if self._matches_gesture(gesture_def, finger_states, hand_landmarks, handedness_label):
                    detected_gestures_this_frame.add(gesture_def.name)

        # Update hold timers
        # Start timer for newly detected gestures
        for gname in detected_gestures_this_frame:
            if gname not in self._hold_start:
                self._hold_start[gname] = now

        # Remove timers for gestures no longer detected
        expired = [g for g in self._hold_start if g not in detected_gestures_this_frame]
        for g in expired:
            del self._hold_start[g]

        # Check if any gesture has been held long enough
        best_gesture = None
        best_progress = 0.0

        for gname, start_time in list(self._hold_start.items()):
            elapsed = now - start_time
            progress = min(elapsed / self.hold_seconds, 1.0)
            if progress > best_progress:
                best_progress = progress
                best_gesture = gname
            if elapsed >= self.hold_seconds:
                self._confirmed_gesture = gname
                # Reset hold to prevent repeated triggers
                del self._hold_start[gname]
                self._last_detected_name = gname
                self._last_hold_progress = 1.0
                return gname

        # Update drawing state
        self._last_detected_name = best_gesture
        self._last_hold_progress = best_progress
        return None

    def draw_feedback(self, frame_bgr: np.ndarray) -> np.ndarray:
        """
        Draw gesture recognition feedback on the frame.

        Shows:
        - Hand landmarks and connections
        - Detected gesture label
        - Hold progress bar

        Parameters
        ----------
        frame_bgr : np.ndarray
            BGR frame to draw on (modified in-place and returned).

        Returns
        -------
        np.ndarray
            The same frame with feedback drawn.
        """
        h, w = frame_bgr.shape[:2]

        # Draw hand landmarks
        for hand_landmarks in self._last_hand_landmarks:
            _draw_hand_landmarks(frame_bgr, hand_landmarks)

        # Draw gesture status
        if self._last_detected_name:
            # Find the gesture definition for color/label
            gdef = next(
                (g for g in self._gestures if g.name == self._last_detected_name),
                None,
            )
            if gdef:
                label = gdef.display_label
                color = gdef.color

                # Gesture label text
                if self._last_hold_progress >= 1.0:
                    status_text = f"{label} - CONFIRMED!"
                else:
                    pct = int(self._last_hold_progress * 100)
                    status_text = f"{label} - Hold... {pct}%"

                # Draw text background
                text_size = cv2.getTextSize(status_text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
                text_x = (w - text_size[0]) // 2
                text_y = h - 60

                cv2.rectangle(
                    frame_bgr,
                    (text_x - 10, text_y - text_size[1] - 10),
                    (text_x + text_size[0] + 10, text_y + 10),
                    (0, 0, 0),
                    -1,
                )
                cv2.putText(
                    frame_bgr,
                    status_text,
                    (text_x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    color,
                    2,
                )

                # Draw progress bar
                bar_y = h - 35
                bar_x_start = w // 4
                bar_width = w // 2
                bar_height = 12

                # Background
                cv2.rectangle(
                    frame_bgr,
                    (bar_x_start, bar_y),
                    (bar_x_start + bar_width, bar_y + bar_height),
                    (60, 60, 60),
                    -1,
                )
                # Fill
                fill_width = int(bar_width * self._last_hold_progress)
                if fill_width > 0:
                    cv2.rectangle(
                        frame_bgr,
                        (bar_x_start, bar_y),
                        (bar_x_start + fill_width, bar_y + bar_height),
                        color,
                        -1,
                    )
                # Border
                cv2.rectangle(
                    frame_bgr,
                    (bar_x_start, bar_y),
                    (bar_x_start + bar_width, bar_y + bar_height),
                    (200, 200, 200),
                    1,
                )

        return frame_bgr

    def close(self) -> None:
        """Release MediaPipe Hand Landmarker resources."""
        self._landmarker.close()
        print("[GestureRecognizer] Closed.")

    # ── Internal helpers ────────────────────────────────────────────────

    def _matches_gesture(
        self,
        gesture_def: GestureDefinition,
        finger_states: Dict[str, bool],
        landmarks,
        handedness: str,
    ) -> bool:
        """Check if current finger states match a gesture definition."""

        # If a custom detector is provided, use it
        if gesture_def.custom_detector is not None:
            return gesture_def.custom_detector(landmarks, handedness)

        # Standard finger-state matching
        required = gesture_def.finger_states
        if not required:
            return False

        matches = sum(
            1 for finger, expected_state in required.items()
            if finger in finger_states and finger_states[finger] == expected_state
        )
        total = len(required)

        if gesture_def.min_fingers_matching is not None:
            return matches >= gesture_def.min_fingers_matching

        return matches == total


# ═══════════════════════════════════════════════════════════════════════════
#  Standalone test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n=== Gesture Recognition Test ===")
    print("Hold LEFT hand open palm for 3 s → START")
    print("Hold LEFT hand peace sign (index+middle) → STOP")
    print("Press 'q' to quit.\n")

    gr = GestureRecognizer(hold_seconds=3.0)
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("Could not open webcam.")
    else:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            gesture = gr.detect(frame)
            gr.draw_feedback(frame)

            if gesture:
                print(f"CONFIRMED: {gesture}")

            cv2.imshow("Gesture Recognition Test", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        cap.release()
        cv2.destroyAllWindows()
        gr.close()
