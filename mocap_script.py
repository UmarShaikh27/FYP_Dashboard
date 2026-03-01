"""
MediaPipe 2D to 3D Pose Estimation Script
==========================================
Gets 2D coordinates using MediaPipe (like mp_webcam.py) and converts to 3D 
using the pretrained LinearModel. Saves both 2D and 3D coordinates to Excel.

Controls:
  - Press 's' to start/pause recording
  - Press 'q' to quit and save data
"""

import cv2
import mediapipe as mp
import pandas as pd
import numpy as np
import time
import os
from datetime import datetime
from scipy.signal import savgol_filter

# ============ CONFIGURATION VARIABLES ============
CAMERA_INDEX = 2
# Use Intel RealSense depth stream for Z values when True. Requires `pyrealsense2`.
USE_REALSENSE_DEPTH = True

# When True, fall back to model output if RealSense depth fails for a joint.
# When False, use the last recorded pose value instead (avoids model computation).
FALLBACK_TO_MODEL_ON_DEPTH_FAILURE = False

# Output folder for saving files
OUTPUT_FOLDER = "output_excel"

# Output Excel file prefix (timestamp will be added automatically)
# OUTPUT_EXCEL_PREFIX = "pose_2d_3d_coordinates"
OUTPUT_EXCEL_PREFIX = "pose"

# Recording duration in seconds (set to None for manual stop with 'q' key)
RECORDING_DURATION = 8

# Frame rate for processing (lower = less data points)
PROCESS_EVERY_N_FRAMES = 1

# Show visualization window
SHOW_VISUALIZATION = True

# Apply bone length normalization from CTransform
APPLY_BONE_TRANSFORM = True

#   'uniform': Simple uniform scaling - all joints scaled equally relative to hip
#   'fk': Forward kinematics approach - preserves joint angles while adjusting bone lengths
BONE_NORM_METHOD = 'fk'  # Change to 'fk' to use forward kinematics approach

# Enable Savitzky-Golay filtering for smoothing 3D motion capture data
ENABLE_SMOOTHING = False
# Savitzky-Golay filter parameters
# window_length: must be odd and <= data length; use ~11 for typical data
SAVGOL_WINDOW = 11
SAVGOL_POLYORDER = 2

def draw_body_landmarks(frame, pose_landmarks, mp_pose):
    """
    Draw only body landmarks, excluding face and hand finger points.
    """
    if pose_landmarks is None:
        return
    
    h, w, _ = frame.shape
    landmarks = pose_landmarks.landmark
    
    # Body landmark indices
    body_indices = [11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32]
    
    # Body connections
    body_connections = [
        (11, 12),  # Shoulders
        (11, 13),  # Left shoulder to left elbow
        (13, 15),  # Left elbow to left wrist
        (12, 14),  # Right shoulder to right elbow
        (14, 16),  # Right elbow to right wrist
        (11, 23),  # Left shoulder to left hip
        (12, 24)   # Right shoulder to right hip
    ]
    
    # Draw connections (lines)
    for start_idx, end_idx in body_connections:
        start = landmarks[start_idx]
        end = landmarks[end_idx]
        
        if start.visibility > 0.5 and end.visibility > 0.5:
            start_point = (int(start.x * w), int(start.y * h))
            end_point = (int(end.x * w), int(end.y * h))
            cv2.line(frame, start_point, end_point, (0, 255, 0), 2)
    
    # Draw landmarks (circles)
    for idx in body_indices:
        landmark = landmarks[idx]
        if landmark.visibility > 0.5:
            cx, cy = int(landmark.x * w), int(landmark.y * h)
            cv2.circle(frame, (cx, cy), 5, (0, 0, 255), -1)

def apply_savgol_smoothing(data_dict, window_length, polyorder):

    """
    Apply Savitzky-Goyal filter to smooth motion capture data.
    Operates on all 3D coordinate columns in the data dictionary.

    Args:
        data_dict: Dict with lists of coordinates {col_name: [values, ...], ...}
        window_length: Window size for filter (must be odd, <= data length)
        polyorder: Polynomial order (1-3 recommended)

    Returns:
        Smoothed dictionary with same structure as input
    """
    smoothed = {}
    n_samples = len(next(iter(data_dict.values())))

    # Only apply filter if we have enough data points
    if n_samples < window_length:
        print(f"Warning: Only {n_samples} samples, need at least {window_length} for Savitzky-Golay filter.")
        print("Returning unsmoothed data.")
        return data_dict

    for col_name, values in data_dict.items():
        if col_name == 'Timestamp':
            smoothed[col_name] = values
        else:
            # Apply Savitzky-Golay filter to numeric columns
            try:
                smoothed[col_name] = savgol_filter(values, window_length, polyorder).tolist()
            except Exception as e:
                print(f"Warning: Could not smooth {col_name}, keeping original. Error: {e}")
                smoothed[col_name] = values

    return smoothed

def search_depth_neighborhood(depth_frame, u, v, frame_width,frame_height,radius=5):
    """Search for valid depth in surrounding pixels"""
    depths = []
    for du in range(-radius, radius + 1):
        for dv in range(-radius, radius + 1):
            test_u, test_v = u + du, v + dv
            if 0 <= test_u < frame_width and 0 <= test_v < frame_height:
                d = depth_frame.get_distance(test_u, test_v)
                if d > 0 and not np.isnan(d):
                    depths.append(d)
    return np.median(depths) if depths else None

def get_pose_from_camera(landmarks, depth_frame, intrinsics, width, height):
    """
    Extract full 17-joint skeleton from RealSense depth camera.
    Returns (17, 3) array with NaN for failed joints.
    """
    pose = np.full((17, 3), np.nan)
    
    # Real joints mapping
    real_joints = {
        1: 24, 2: 26, 3: 28,  # Right leg
        4: 23, 5: 25, 6: 27,  # Left leg
        10: 0,  # Head
        11: 11, 12: 13, 13: 15,  # Left arm
        14: 12, 15: 14, 16: 16   # Right arm
    }
    
    # Get real joints
    for h36m_idx, mp_idx in real_joints.items():
        lm = landmarks[mp_idx]
        u, v = int(lm.x * width), int(lm.y * height)
        
        if 0 <= u < width and 0 <= v < height:
            depth_m = depth_frame.get_distance(u, v)
            if depth_m <= 0 or np.isnan(depth_m):
                depth_m = search_depth_neighborhood(depth_frame ,u, v, width,height)
            else:
                pose[h36m_idx] = [
                    ((u - intrinsics.ppx) / intrinsics.fx * depth_m),
                    ((v - intrinsics.ppy) / intrinsics.fy * depth_m),
                    depth_m
                ]
    
    return pose

def main():
    # Initialize MediaPipe Pose
    mp_pose = mp.solutions.pose
    
    # Data storage lists
    timestamps = []
    
    # Data storage lists for 3D coordinates only (right hand only)   
    right_wrist_3d_x, right_wrist_3d_y, right_wrist_3d_z = [], [], []

    # Initialize 2D-to-3D converter
    print("Loading 2D-to-3D model...")

    # Open camera (either OpenCV capture or RealSense pipeline)
    realsense_pipeline = None
    realsense_align = None
    if USE_REALSENSE_DEPTH:
        try:
            import pyrealsense2 as rs
        except Exception as e:
            print("Error: pyrealsense2 not installed or failed to import.")
            return

        print("Starting Intel RealSense pipeline (color+depth)...")
        realsense_pipeline = rs.pipeline()
        cfg = rs.config()
        # Let the device choose resolution; common defaults below
        cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
        cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
        profile = realsense_pipeline.start(cfg)
        realsense_align = rs.align(rs.stream.color)
        # Get intrinsics from depth stream later inside loop when frames are available
        # Use placeholder resolution until first frames arrive
        frame_width, frame_height = 640, 480
        print(f"RealSense started. Approx resolution: {frame_width}x{frame_height}")
    else:
        print(f"Opening camera at index {CAMERA_INDEX}...")
        cap = cv2.VideoCapture(CAMERA_INDEX)

        if not cap.isOpened():
            print(f"Error: Could not open camera at index {CAMERA_INDEX}")
            print("Try changing CAMERA_INDEX to a different value (0, 1, 2, etc.)")
            return

        # Get frame dimensions
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"Camera opened successfully. Resolution: {frame_width}x{frame_height}")
    print("Press 'q' to stop recording and save data.")
    print("Press 's' to start/pause recording.")

    # Initialize pose model
    pose = mp_pose.Pose(
        model_complexity=1,
        smooth_landmarks= True
    )

    start_time = time.time()
    frame_count = 0
    is_recording = True
    last_pose_3d = np.zeros((17, 3))  # Track last pose for fallback when model is disabled

    try:
        while True:
            if USE_REALSENSE_DEPTH:
                # Get frames from RealSense
                frames = realsense_pipeline.wait_for_frames()
                aligned_frames = realsense_align.process(frames)
                depth_frame = aligned_frames.get_depth_frame()
                color_frame = aligned_frames.get_color_frame()
                if not depth_frame or not color_frame:
                    print("Warning: Incomplete RealSense frames, skipping")
                    continue
                frame = np.asanyarray(color_frame.get_data())
                # update frame dims from actual frames
                frame_width = color_frame.get_width()
                frame_height = color_frame.get_height()
                depth_intrinsics = depth_frame.get_profile().as_video_stream_profile().get_intrinsics()
            else:
                ret, frame = cap.read()
                if not ret:
                    print("Error: Could not read frame from camera")
                    break

            frame_count += 1

            # Check recording duration
            elapsed_time = time.time() - start_time
            if RECORDING_DURATION is not None and elapsed_time > RECORDING_DURATION:
                print(f"Recording duration of {RECORDING_DURATION}s reached.")
                break

            # Convert BGR to RGB for MediaPipe
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb_frame.flags.writeable = False

            # Process frame with MediaPipe Pose
            results = pose.process(rgb_frame)

            # Convert back to BGR for visualization
            rgb_frame.flags.writeable = True
            frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)

            if results.pose_landmarks and is_recording:
                landmarks = results.pose_landmarks.landmark

                current_time = time.time() - start_time
                timestamps.append(current_time)

                pose_3d = get_pose_from_camera(
                        landmarks, depth_frame, depth_intrinsics, 
                        frame_width, frame_height
                    )

               
                # 3D indices: 14:RShoulder, 15:RElbow, 16:RWrist (right hand only)
                right_wrist_3d_x.append(pose_3d[16][0])
                right_wrist_3d_y.append(pose_3d[16][1])
                right_wrist_3d_z.append(pose_3d[16][2])
    
                # Draw only body landmarks
                draw_body_landmarks(frame, results.pose_landmarks, mp_pose)


            # Show frame
            if SHOW_VISUALIZATION:
                cv2.imshow('MediaPipe 2D to 3D Pose Estimation', frame)

            # Handle key presses
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("Stopping recording...")
                break
            elif key == ord('s'):
                is_recording = not is_recording
                state = "resumed" if is_recording else "paused"
                print(f"Recording {state}")

    except KeyboardInterrupt:
        print("Recording interrupted by user")
    finally:
        # Cleanup
        if USE_REALSENSE_DEPTH:
            try:
                realsense_pipeline.stop()
            except Exception:
                pass
        else:
            try:
                cap.release()
            except Exception:
                pass
        cv2.destroyAllWindows()
        pose.close()

    # Save data to Excel
    if len(timestamps) > 0:
        # Create output folder if it doesn't exist
        if not os.path.exists(OUTPUT_FOLDER):
            os.makedirs(OUTPUT_FOLDER)
            print(f"Created output folder: {OUTPUT_FOLDER}")
        
        # Generate filename with timestamp
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(OUTPUT_FOLDER, f"{OUTPUT_EXCEL_PREFIX}_{timestamp_str}.xlsx")
        
        print(f"Saving {len(timestamps)} data points to {output_file}...")
        
        # Prepare data dictionary (right hand only)
        data_dict = {
            'Wrist_x': right_wrist_3d_x,
            'Wrist_y': right_wrist_3d_y,
            'Wrist_z': right_wrist_3d_z,
        }

    
        # Apply Savitzky-Golay smoothing if enabled
        if ENABLE_SMOOTHING and len(timestamps) >= SAVGOL_WINDOW:
            print(f"Applying Savitzky-Golay smoothing (window={SAVGOL_WINDOW}, polyorder={SAVGOL_POLYORDER})...")
            data_dict = apply_savgol_smoothing(data_dict, SAVGOL_WINDOW, SAVGOL_POLYORDER)
        
        # Create DataFrame with all data
        df = pd.DataFrame(data_dict)
        df['Frame_Width'] = [frame_width] * len(timestamps)
        df['Frame_Height'] = [frame_height] * len(timestamps)

        # Save single sheet with all data
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='All_Data', index=False)

        print(f"Data saved successfully to {output_file} (sheet: All_Data)")
        print(f"Total recording time: {timestamps[-1]:.2f} seconds")
    else:
        print("No data recorded. Excel file not created.")


if __name__ == "__main__":
    main()
