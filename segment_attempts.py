import os
import shutil
import numpy as np
import pandas as pd

def segment_attempts(
    normalized_excel_path: str,
    output_dir: str,
    n_attempts: int | None,
    min_gap_seconds: float = 1.0,
    rest_velocity_percentile: float = 30.0,
    min_attempt_seconds: float = 3.0,
    edge_trim_seconds: float = 0.2,
    exercise_type: str = "eight_tracing",
) -> list[str]:
    """
    Slice a normalized Excel file into n_attempts individual attempt files.

    Parameters
    ----------
    edge_trim_seconds : float
        Seconds of low-velocity frames to trim from the start and end of
        each segment.  Removes rest/transition artifacts that the filter
        stage can't catch because they span many frames.

    Algorithm:
      1. Load the normalized Excel. It has wrist_normalized_x/y/z columns.
      2. Compute per-frame 3D velocity of the normalized wrist signal.
         velocity[i] = sqrt( dx² + dy² + dz² ) where dx = x[i] - x[i-1].
         Set velocity[0] = 0.
      3. Apply a rolling minimum over a window equivalent to min_gap_seconds
         (assume 30 fps; window = int(min_gap_seconds * 30)).
      4. Compute the rest threshold: the rest_velocity_percentile-th
         percentile of the raw velocity array.
      5. Mark frames as "rest" where the rolling minimum is below the
         rest threshold. Consecutive rest frames form rest segments.
      6. Find rest segments whose duration >= min_gap_seconds * 30 frames.
         These are the candidate boundaries between attempts.
      7. From the candidate boundaries, select exactly (n_attempts - 1)
         boundaries by taking the longest rest segments (by frame count).
         Sort the selected boundaries by position to get ordered split points.
      8. Add frame 0 as the start of attempt 1 and the last frame as the
         end of attempt N.
      9. For each of the N attempt slices, use the midpoint of the
         corresponding rest segment as the actual split frame. The attempt
         slice runs from the previous split frame to this split frame
         (non-overlapping, together they cover the full recording).
     10. Trim low-velocity edge frames from the start and end of each slice
         to remove rest/transition artifacts.
     11. Validate each slice has >= min_attempt_seconds * 30 frames.
         If any slice is too short, raise a ValueError with a clear message
         telling the user to either reduce n_attempts or re-record with
         longer attempts and clearer pauses between them.
     12. Save each slice as a separate Excel file:
           output_dir/attempt_1/segment.xlsx
           output_dir/attempt_2/segment.xlsx
           ... (1-indexed)
         Each saved file is the original DataFrame rows for that slice,
         preserving ALL original columns exactly.
     13. Return a list of the saved file paths in attempt order.
    """
    # SPECIAL CONFIG OVERRIDES FOR EXERCISES
    if exercise_type == "flexion_2kg":
        min_gap_seconds = min(min_gap_seconds, 0.3)
        min_attempt_seconds = min(min_attempt_seconds, 1.0)
        print(f"[Segmentation] FLEXION 2KG config override: min_gap_seconds={min_gap_seconds}, min_attempt_seconds={min_attempt_seconds}")

    fps = 30
    
    if n_attempts == 1:
        attempt_dir = os.path.join(output_dir, "attempt_1")
        os.makedirs(attempt_dir, exist_ok=True)
        out_path = os.path.join(attempt_dir, "segment.xlsx")
        shutil.copy2(normalized_excel_path, out_path)
        return [out_path]

    # 1. Load the normalized Excel
    df = pd.read_excel(normalized_excel_path)
    
    # 2. Compute per-frame 3D velocity
    x = df["wrist_normalized_x"].values
    y = df["wrist_normalized_y"].values
    z = df["wrist_normalized_z"].values
    
    dx = np.diff(x, prepend=x[0])
    dy = np.diff(y, prepend=y[0])
    dz = np.diff(z, prepend=z[0])
    
    velocity = np.sqrt(dx**2 + dy**2 + dz**2)
    velocity[0] = 0.0
    
    # 2.5 Smooth velocity to remove jitter and make pauses obvious
    smoothed_velocity = pd.Series(velocity).rolling(window=15, center=True, min_periods=1).mean().values
    
    # 3. Apply a rolling minimum
    window = int(min_gap_seconds * fps)
    # Using a centered window as it aligns well with gap detection
    rolling_min = pd.Series(smoothed_velocity).rolling(window=window, center=True, min_periods=1).min().values
    
    # 4. Compute the rest threshold
    rest_threshold = np.percentile(smoothed_velocity, rest_velocity_percentile)
    
    # 5. Mark frames as rest
    is_rest = rolling_min < rest_threshold
    
    # 6. Find all contiguous rest segments
    all_segments = []
    in_segment = False
    start_idx = 0
    for i in range(len(is_rest)):
        if is_rest[i] and not in_segment:
            in_segment = True
            start_idx = i
        elif not is_rest[i] and in_segment:
            in_segment = False
            duration = i - start_idx
            if duration >= window:
                all_segments.append((start_idx, i, duration))
    if in_segment:
        duration = len(is_rest) - start_idx
        if duration >= window:
            all_segments.append((start_idx, len(is_rest), duration))

    # 7. Identify leading / trailing buffers and filter true inter-attempt gaps
    leading_rest_end = 0
    trailing_rest_start = len(df)
    
    segments = []
    for s, e, d in all_segments:
        if s <= 15:  # This rest touches the very beginning (leading buffer)
            leading_rest_end = e
        elif e >= len(df) - 15:  # This rest touches the very end (trailing buffer)
            trailing_rest_start = s
        else:
            segments.append((s, e, d))

    print(f"[Segmentation] Rest threshold: {rest_threshold:.6f}")
    if leading_rest_end > 0:
        print(f"  -> Ignored leading rest buffer (0–{leading_rest_end})")
    if trailing_rest_start < len(df):
        print(f"  -> Ignored trailing rest buffer ({trailing_rest_start}–{len(df)})")
        
    print(f"  Total valid inter-attempt rest gaps found: {len(segments)}")
    for idx, (s, e, d) in enumerate(segments):
        print(f"    Gap {idx+1}: frames {s}–{e} ({d} frames, {d/fps:.1f}s)")

    # SPECIAL LOGIC FOR FLEXION 2KG
    if exercise_type == "flexion_2kg" and len(segments) >= 1:
        print("[Segmentation] Applying FLEXION 2KG special logic: merging two-peak attempts by dropping intra-attempt rest gaps.")
        # We only keep every alternating gap (i.e., true inter-attempt rest, missing the mid-point top pause)
        segments = segments[1::2]
        print(f"  Total valid inter-attempt rest gaps after Flexion filtering: {len(segments)}")

    # 8. Select exactly (n_attempts - 1) boundaries if n_attempts is provided
    if n_attempts is not None:
        if len(segments) < n_attempts - 1:
            raise ValueError(
                f"Could not detect {n_attempts - 1} rest boundaries inside the active session.\n"
                f"Found only {len(segments)} valid gaps. Try: (1) pausing more clearly,\n"
                f"(2) reducing N_ATTEMPTS, or (3) reducing MIN_GAP_SECONDS."
            )
        
        # Sort by duration, keep the largest valid gaps
        segments.sort(key=lambda val: val[2], reverse=True)
        selected_segments = segments[:n_attempts - 1]
        actual_attempts = n_attempts
    else:
        # Use all detected inter-attempt gaps
        selected_segments = segments
        actual_attempts = len(segments) + 1
    
    # Sort selected boundaries chronologically
    selected_segments.sort(key=lambda val: val[0])
    
    # 9. Find midpoints and set split points for slicing
    split_points = [leading_rest_end]
    for seg in selected_segments:
        seg_start, seg_end, seg_duration = seg
        midpoint = (seg_start + seg_end) // 2
        split_points.append(midpoint)
        print(f"[Segmentation] Split boundary at frame {midpoint} (t={midpoint/fps:.1f}s)  gap={seg_duration} fr")
        
    split_points.append(trailing_rest_start)
    
    # 10. Trim edges + Validate and slice
    saved_paths = []
    min_frames = int(min_attempt_seconds * fps)
    trim_frames = int(edge_trim_seconds * fps)
    
    for i in range(actual_attempts):
        start_frame = split_points[i]
        end_frame = split_points[i+1]

        # ── Edge trimming: remove low-velocity rest/transition frames ──
        if trim_frames > 0:
            seg_velocity = velocity[start_frame:end_frame]
            seg_median_vel = np.median(seg_velocity)

            # Trim leading low-velocity frames (up to trim_frames)
            trimmed_start = 0
            for j in range(min(trim_frames, len(seg_velocity))):
                if seg_velocity[j] < seg_median_vel * 0.3:
                    trimmed_start = j + 1
                else:
                    break

            # Trim trailing low-velocity frames (up to trim_frames)
            trimmed_end = len(seg_velocity)
            for j in range(len(seg_velocity) - 1,
                           max(len(seg_velocity) - 1 - trim_frames, -1), -1):
                if seg_velocity[j] < seg_median_vel * 0.3:
                    trimmed_end = j
                else:
                    break

            actual_start = start_frame + trimmed_start
            actual_end = start_frame + trimmed_end

            if actual_end - actual_start >= min_frames:
                start_frame = actual_start
                end_frame = actual_end
                if trimmed_start > 0 or trimmed_end < len(seg_velocity):
                    print(f"[Segmentation] Attempt {i+1}: trimmed {trimmed_start} leading + "
                          f"{len(seg_velocity) - trimmed_end} trailing rest frames")

        attempt_dur_frames = end_frame - start_frame
        if attempt_dur_frames < min_frames:
            raise ValueError(
                f"Attempt {i+1} is too short ({attempt_dur_frames} frames, "
                f"req. >= {min_frames}). Try either to reduce n_attempts or "
                f"re-record with longer attempts and clearer pauses between them."
            )
                             
        # Slice dataframe
        slice_df = df.iloc[start_frame:end_frame].copy()
        
        # 11. Save each slice
        attempt_number = i + 1
        attempt_dir = os.path.join(output_dir, f"attempt_{attempt_number}")
        os.makedirs(attempt_dir, exist_ok=True)
        out_path = os.path.join(attempt_dir, "segment.xlsx")
        
        slice_df.to_excel(out_path, index=False)
        saved_paths.append(out_path)
        
        print(f"[Segmentation] Attempt {attempt_number}: frames {start_frame}–{end_frame}  ({attempt_dur_frames/fps:.1f}s)")
        
    # 12. Return
    return saved_paths

if __name__ == "__main__":
    test_template_path = "right_wrist_template_10_trails_demo_bone_normalized.xlsx"
    test_out_dir = "test_segments"
    if os.path.exists(test_template_path):
        paths = segment_attempts(test_template_path, test_out_dir, n_attempts=3)
        print("Returned paths:", paths)
    else:
        print(f"Test template {test_template_path} not found.")

