"""
score.py - mDTW + SPARC hierarchical scoring module for the unified physiotherapy pipeline.

This file mirrors the scoring logic used in:
  `Scoring_Module/disected_mmDTW.ipynb`

Specifically, it uses:
- ROM metrics:
  avg_rom_ratio = mean(ptp(patient)/ptp(reference)) with ptp=peak-to-peak
- ROM grading:
  get_rom_grade(ratio) thresholds exactly as in the notebook
- Shape (mDTW) scoring:
  mean-centering ONLY, then constrained mDTW path using:
    dtw_path(..., global_constraint="sakoe_chiba", sakoe_chiba_radius=radius)
- global_rmse:
  global_rmse = sim_dist / sqrt(path_length)
- final score:
    The system now uses a hierarchical 0-10 scoring format with
    configurable weights loaded from scoring_weights.json.

Inputs (Excel columns)
----------------------
patient_filtered_path:
  Must contain: `Wrist_x`, `Wrist_y`, `Wrist_z`
scaled_template_path:
  Must contain: `wrist_scaled_x`, `wrist_scaled_y`, `wrist_scaled_z`

Outputs (written under output_dir)
----------------------------------
- `score_results.xlsx`:
  - sheet `scores`
  - sheet `sparc_raw`
  - sheet `feedback`
  - sheet `dtw_alignment`
  - sheet `report`
- `score_plot.png`:
  trajectory overlay + therapist dashboard plot (notebook-style)
- `patient_view.png`:
  hierarchical patient-facing score card
- `therapist_view.png`:
  full therapist analytics dashboard

Public API
----------
- compute_score(patient_filtered_path, scaled_template_path, output_dir, ...) -> dict
- score_movement(patient_filtered_path, template_scaled_path, output_dir, ...) -> dict
- load_weights(weights_path) -> dict
"""

import json
import io
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")
import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")  # headless backend for saving plots
import matplotlib.pyplot as plt
from scipy.fft import fft, fftfreq
from scipy.signal import butter, filtfilt, resample

from mpl_toolkits.mplot3d import Axes3D  # noqa: F401


# ═══════════════════════════════════════════════════════════════════════════
#  Configurable weights
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_WEIGHTS = {
    "som": 1.0,
    "rom": 1.0,
    "tremor": 0.1,
    "hesitation": 0.1,
    "control": 0.1,
    "velocity_profile": 0.1,
}


def load_weights(weights_path: str | None = None) -> dict:
    """
    Load scoring weights from a JSON file.

    Parameters
    ----------
    weights_path : str | None
        Path to the JSON weights file. If None, looks for
        `scoring_weights.json` in the same directory as this script.

    Returns
    -------
    dict
        Weight values for each scoring component.
    """
    if weights_path is None:
        weights_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "scoring_weights.json",
        )

    if os.path.isfile(weights_path):
        with open(weights_path, "r") as f:
            loaded = json.load(f)
        # Merge with defaults (in case user removed a key)
        weights = {**DEFAULT_WEIGHTS, **loaded}
        print(f"[INFO] Loaded scoring weights from: {weights_path}")
    else:
        weights = DEFAULT_WEIGHTS.copy()
        print(f"[WARN] Weights file not found at {weights_path}, using defaults.")

    return weights


def weighted_average(scores: dict, weights: dict) -> float:
    """
    Compute weighted average of scores.

    Parameters
    ----------
    scores : dict
        Mapping of component name -> score value (0-10).
    weights : dict
        Mapping of component name -> weight.

    Returns
    -------
    float
        Weighted average, rounded to 1 decimal.
    """
    total_weight = 0.0
    total_score = 0.0
    for key, score_val in scores.items():
        w = weights.get(key, 0.0)
        total_score += w * float(score_val)
        total_weight += w
    if total_weight == 0:
        return 0.0
    return round(total_score / total_weight, 1)


# Notebook defaults/config
SENSITIVITY_DEFAULT = 3.0
DTW_RADIUS_DEFAULT = 10
SHAPE_TOLERANCE_M_DEFAULT = 0.20

# SPARC notebook constants (from disected_SPARC.ipynb)
FREQ_LIMIT_LOW = 5.0
FREQ_LIMIT_HIGH = 20.0
SAMPLE_RATE_DEFAULT = 30.0
FILTER_FREQ_DEFAULT = 14.0
SPARC_THRESHOLD_PERCENT_DEFAULT = 0.30
ENABLE_SPARC_FILTERING_DEFAULT = True
JERK_IQR_MULTIPLIER_DEFAULT = 4.0
VELOCITY_BUFFER_PCT_DEFAULT = 0.0  # 0.10 = ±10% of template length as lead/lag tolerance

# ── SPARC sub-score grading: exponential decay ────────────────────────
# Formula:  score = 10 * exp(-k * |diff|)
# Default:  k = ln(10) / threshold  →  score ≈ 1 when diff == threshold
TREMOR_THRESHOLD_DEFAULT     = 2.0   # high-band SPARC |diff| for score ≈ 1
HESITATION_THRESHOLD_DEFAULT = 2.0   # low-band  SPARC |diff| for score ≈ 1
VELPROFILE_THRESHOLD_DEFAULT = 0.5   # velocity RMSE (m/s)     for score ≈ 1
CONTROL_MAX_OUTLIERS_DEFAULT = 10    # jerk outlier count      for score ≈ 1


TEMPLATE_COLS = ["wrist_scaled_x", "wrist_scaled_y", "wrist_scaled_z"]
PATIENT_COLS = ["Wrist_x", "Wrist_y", "Wrist_z"]
PATIENT_FILTERED_NORMALIZED_COLS = ["wrist_normalized_x", "wrist_normalized_y", "wrist_normalized_z"]
PATIENT_SHOULDER_COLS = ["Shoulder_x", "Shoulder_y", "Shoulder_z"]


def _require_columns(df: pd.DataFrame, required: List[str], name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")


def _extract_patient_global_trajectory_from_filtered(df: pd.DataFrame) -> Tuple[np.ndarray, str]:
    """
    Build patient global trajectory from the filtered file.

    Priority:
    1) Use filtered normalized wrist + shoulder + total_arm_length
       (ensures scoring uses 3-stage filtered output).
    2) Fallback to Wrist_x/y/z if normalized reconstruction columns are absent.
    """
    has_norm = all(c in df.columns for c in PATIENT_FILTERED_NORMALIZED_COLS)
    has_shoulder = all(c in df.columns for c in PATIENT_SHOULDER_COLS)
    has_arm = "total_arm_length" in df.columns

    if has_norm and has_shoulder and has_arm:
        req_cols = PATIENT_FILTERED_NORMALIZED_COLS + PATIENT_SHOULDER_COLS + ["total_arm_length"]
        temp = df.dropna(subset=req_cols).copy()
        if len(temp) < 2:
            raise ValueError("Insufficient rows after dropna when reconstructing filtered global trajectory.")

        wx = temp["wrist_normalized_x"].to_numpy(dtype=float) * temp["total_arm_length"].to_numpy(dtype=float) + temp["Shoulder_x"].to_numpy(dtype=float)
        wy = temp["wrist_normalized_y"].to_numpy(dtype=float) * temp["total_arm_length"].to_numpy(dtype=float) + temp["Shoulder_y"].to_numpy(dtype=float)
        wz = temp["wrist_normalized_z"].to_numpy(dtype=float) * temp["total_arm_length"].to_numpy(dtype=float) + temp["Shoulder_z"].to_numpy(dtype=float)
        return np.column_stack([wx, wy, wz]), "reconstructed_from_filtered_normalized"

    # Fallback
    _require_columns(df, PATIENT_COLS, "Patient")
    temp = df.dropna(subset=PATIENT_COLS).copy()
    if len(temp) < 2:
        raise ValueError("Insufficient non-NaN rows in patient Wrist_x/y/z columns.")
    return temp[PATIENT_COLS].to_numpy(dtype=float), "raw_wrist_fallback"


def add_awgn(data: np.ndarray, std_dev: float = 0.01) -> np.ndarray:
    """Adds Additive White Gaussian Noise to simulate tremor/jitter (not used in pipeline)."""
    noise = np.random.normal(0, std_dev, data.shape)
    return data + noise


class MovementAnalyzer:
    """
    Ported from `Scoring_Module/disected_SPARC.ipynb`.
    """

    def __init__(
        self,
        fs: float = SAMPLE_RATE_DEFAULT,
        cutoff: float = FILTER_FREQ_DEFAULT,
        jerk_iqr_multiplier: float = JERK_IQR_MULTIPLIER_DEFAULT,
    ):
        self.fs = fs
        self.cutoff = cutoff
        self.jerk_iqr_multiplier = jerk_iqr_multiplier

    def _low_pass_filter(self, data: np.ndarray) -> np.ndarray:
        """Applies 4th order Butterworth filter to remove camera jitter."""
        nyq = 0.5 * self.fs
        normal_cutoff = self.cutoff / nyq
        b, a = butter(4, normal_cutoff, btype="low", analog=False)

        if len(data) <= 15:  # too short for filtfilt padlen — return unfiltered
            return data

        filtered_data = np.zeros_like(data)
        for i in range(data.shape[1]):  # Iterate x, y, z
            filtered_data[:, i] = filtfilt(b, a, data[:, i])
        return filtered_data

    def _get_speed_profile(self, positions: np.ndarray) -> np.ndarray:
        """Calculates Euclidean speed from 3D positions."""
        velocity = np.diff(positions, axis=0) * self.fs
        velocity = np.vstack([np.zeros(3), velocity])  # match original length
        speed = np.sqrt(np.sum(velocity**2, axis=1))
        return speed

    def get_spectrum(self, speed_profile: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Helper to get spectrum data for plotting."""
        N = len(speed_profile)
        pad_level = 4
        n_fft = int(2 ** (np.ceil(np.log2(N)) + pad_level))

        spectrum = np.abs(fft(speed_profile, n_fft))
        dc = spectrum[0]
        if dc == 0:
            dc = 1.0  # avoid div by zero

        norm_spec = spectrum / dc
        freqs = fftfreq(n_fft, d=1 / self.fs)

        mask_pos = freqs >= 0
        return freqs[mask_pos], norm_spec[mask_pos]

    def calculate_sparc_components(self, speed_profile: np.ndarray) -> Tuple[float, float, float]:
        """Calculates Overall, Low-Band, and High-Band SPARC scores."""
        freqs, norm_spec = self.get_spectrum(speed_profile)

        # Define bands
        mask_total = freqs <= FREQ_LIMIT_HIGH
        mask_low = freqs <= FREQ_LIMIT_LOW
        mask_high = (freqs > FREQ_LIMIT_LOW) & (freqs <= FREQ_LIMIT_HIGH)

        # Arc length calculation helper
        def get_arc_length(f_roi: np.ndarray, s_roi: np.ndarray) -> float:
            if len(f_roi) < 2:
                return 0.0
            df = f_roi[1] - f_roi[0]
            ds = np.diff(s_roi)
            # Scale factor (1/wc)^2 where wc = 20Hz (40pi)
            wc = 40 * np.pi
            integrand = np.sqrt((1 / wc) ** 2 + (ds / df) ** 2)
            return -np.sum(integrand) * df

        sparc_total = get_arc_length(freqs[mask_total], norm_spec[mask_total])
        sparc_low = get_arc_length(freqs[mask_low], norm_spec[mask_low])
        sparc_high = get_arc_length(freqs[mask_high], norm_spec[mask_high])
        return float(sparc_total), float(sparc_low), float(sparc_high)

    def compare_performances(self, ref_pos: np.ndarray, pat_pos: np.ndarray, use_filter: bool = True) -> Dict:
        # 1. Pre-process
        if use_filter:
            ref_final = self._low_pass_filter(ref_pos)
            pat_final = self._low_pass_filter(pat_pos)
            print(f"[INFO] Applied Low-Pass Filter ({self.cutoff} Hz)")
        else:
            ref_final = ref_pos
            pat_final = pat_pos
            print("[INFO] Using Raw Data (No Filtering)")

        # Calculate speed profiles
        ref_speed = self._get_speed_profile(ref_final)
        pat_speed = self._get_speed_profile(pat_final)

        # 2. SPARC analysis
        ref_sparc, ref_low, ref_high = self.calculate_sparc_components(ref_speed)
        pat_sparc, pat_low, pat_high = self.calculate_sparc_components(pat_speed)

        # 3. Time-domain analysis
        pat_speed_resampled = resample(pat_speed, len(ref_speed))
        mse = np.mean((ref_speed - pat_speed_resampled) ** 2)
        vel_rmse = np.sqrt(mse)

        ref_peak = np.max(ref_speed)
        pat_peak = np.max(pat_speed)
        ref_mean = float(np.mean(ref_speed))
        pat_mean = float(np.mean(pat_speed))

        # Velocity profile lead/lag based on peak timing in matched-length profiles
        ref_peak_idx = int(np.argmax(ref_speed))
        pat_peak_idx_resampled = int(np.argmax(pat_speed_resampled))
        lag_frames = pat_peak_idx_resampled - ref_peak_idx
        lag_seconds = lag_frames / float(self.fs)

        # Sudden peak/drop outliers from first-difference of patient speed
        speed_diff = np.diff(pat_speed)
        if len(speed_diff) > 0:
            q1 = np.percentile(speed_diff, 25)
            q3 = np.percentile(speed_diff, 75)
            iqr = q3 - q1
            upper_jump = q3 + self.jerk_iqr_multiplier * iqr
            lower_drop = q1 - self.jerk_iqr_multiplier * iqr
            jerk_up_indices = np.where(speed_diff > upper_jump)[0]
            jerk_down_indices = np.where(speed_diff < lower_drop)[0]
        else:
            jerk_up_indices = np.array([], dtype=int)
            jerk_down_indices = np.array([], dtype=int)

        # 4. Return metrics + raw data for plotting
        metrics = {
            "Reference": {
                "Total_SPARC": ref_sparc,
                "Low_Band_SPARC": ref_low,
                "High_Band_SPARC": ref_high,
                "Peak_Velocity": float(ref_peak),
                "Mean_Velocity": ref_mean,
            },
            "Patient": {
                "Total_SPARC": pat_sparc,
                "Low_Band_SPARC": pat_low,
                "High_Band_SPARC": pat_high,
                "Peak_Velocity": float(pat_peak),
                "Velocity_RMSE": float(vel_rmse),
                "Mean_Velocity": pat_mean,
                "Velocity_Peak_Lag_Frames": int(lag_frames),
                "Velocity_Peak_Lag_Seconds": float(lag_seconds),
                "Sudden_Peak_Count": int(len(jerk_up_indices)),
                "Sudden_Drop_Count": int(len(jerk_down_indices)),
            },
            "Plot_Data": {
                "Ref_Speed": ref_speed,
                "Pat_Speed": pat_speed,
                "Pat_Speed_Resampled": pat_speed_resampled,
                "Ref_Pos": ref_final,
                "Pat_Pos": pat_final,
            },
        }
        return metrics


def print_clinical_report(metrics: Dict, threshold_pct: float) -> Dict[str, str]:
    """
    Notebook report logic, returned as status flags for saving.
    """
    ref = metrics["Reference"]
    pat = metrics["Patient"]

    print("\n" + "=" * 70)
    print(f"   CLINICAL MOVEMENT ANALYSIS (Threshold: +/- {threshold_pct*100}%)")
    print("=" * 70)
    print(f"{'METRIC':<25} | {'REF':<10} | {'PATIENT':<10} | {'STATUS':<15}")
    print("-" * 70)

    status_flags: Dict[str, str] = {}

    def print_row(name: str, ref_val: float, pat_val: float, limit: float, is_upper_limit: bool = False) -> None:
        if is_upper_limit:
            passed = pat_val < limit
        else:
            passed = pat_val > limit
        status = "PASS" if passed else "FAIL"
        status_flags[name] = status
        print(f"{name:<25} | {ref_val:.4f}     | {pat_val:.4f}     | {status}")

    # 1. Overall SPARC
    sparc_limit = ref["Total_SPARC"] - abs(ref["Total_SPARC"] * threshold_pct)
    print_row("Overall SPARC", ref["Total_SPARC"], pat["Total_SPARC"], sparc_limit)

    # 2. Choppiness (Low Band)
    low_limit = ref["Low_Band_SPARC"] - abs(ref["Low_Band_SPARC"] * threshold_pct)
    print_row("Choppiness (0-5Hz)", ref["Low_Band_SPARC"], pat["Low_Band_SPARC"], low_limit)

    # 3. Tremor (High Band)
    high_limit = ref["High_Band_SPARC"] - abs(ref["High_Band_SPARC"] * threshold_pct)
    print_row("Tremor (5-20Hz)", ref["High_Band_SPARC"], pat["High_Band_SPARC"], high_limit)

    print("-" * 70)

    # 4. Bradykinesia (RMSE)
    rmse_limit = ref["Mean_Velocity"] * threshold_pct
    rmse_status = "PASS" if pat["Velocity_RMSE"] < rmse_limit else "FAIL (Timing)"
    status_flags["Velocity RMSE"] = rmse_status
    print(f"{'Velocity RMSE':<25} | {'0.0000':<10} | {pat['Velocity_RMSE']:.4f}     | {rmse_status}")

    # 5. Spasm (Peak Spike)
    peak_limit = ref["Peak_Velocity"] * (1 + threshold_pct)
    print_row("Peak Velocity", ref["Peak_Velocity"], pat["Peak_Velocity"], peak_limit, is_upper_limit=True)

    print("=" * 70 + "\n")
    return status_flags


def print_patient_feedback(
    metrics: Dict,
    velocity_buffer_pct: float = VELOCITY_BUFFER_PCT_DEFAULT,
    template_frame_count: int = 0,
) -> Dict[str, str]:
    """
    Prints explicit patient-facing checks requested by the user.
    """
    ref = metrics["Reference"]
    pat = metrics["Patient"]
    feedback: Dict[str, str] = {}

    print("=" * 70)
    print("   PATIENT FEEDBACK CHECKS")
    print("=" * 70)

    # 1) 0-5Hz smoothness check
    if pat["Low_Band_SPARC"] < ref["Low_Band_SPARC"]:
        msg1 = "Choppy Movement detected (Hesitation / Submovements)."
    else:
        msg1 = "No choppy movement detected in 0-5 Hz band."
    feedback["check_1_choppy_0_5hz"] = msg1
    print(f"1) {msg1}")

    # 2) 6-20Hz smoothness check (implemented using notebook high band 5-20Hz)
    if pat["High_Band_SPARC"] < ref["High_Band_SPARC"]:
        msg2 = "Shaking / Jitter detected in high-frequency band."
    else:
        msg2 = "No shaking / jitter detected in high-frequency band."
    feedback["check_2_shaking_6_20hz"] = msg2
    print(f"2) {msg2}")

    # 3) Velocity profile leading/lagging => too fast / too slow
    buffer_frames = max(round(template_frame_count * velocity_buffer_pct), 0)
    lag_frames = int(pat["Velocity_Peak_Lag_Frames"])
    lag_seconds = float(pat["Velocity_Peak_Lag_Seconds"])
    if lag_frames > buffer_frames:
        msg3 = (
            f"Velocity profile is lagging by {lag_frames} frames ({lag_seconds:.2f}s): "
            "you are performing too slow."
        )
    elif lag_frames < -buffer_frames:
        msg3 = (
            f"Velocity profile is leading by {abs(lag_frames)} frames ({abs(lag_seconds):.2f}s): "
            "you are performing too fast."
        )
    else:
        msg3 = (
            f"Velocity profile timing is aligned "
            f"(within ±{buffer_frames} frame buffer at {velocity_buffer_pct*100:.0f}%)."
        )
    feedback["check_3_velocity_lead_lag"] = msg3
    print(f"3) {msg3}")

    # 4) Velocity outliers => sudden spasm / jerk
    sudden_peak_count = int(pat["Sudden_Peak_Count"])
    sudden_drop_count = int(pat["Sudden_Drop_Count"])
    if sudden_peak_count > 0 or sudden_drop_count > 0:
        msg4 = (
            f"Sudden spasm/jerk detected: {sudden_peak_count} sudden peak(s), "
            f"{sudden_drop_count} sudden drop(s) in velocity profile."
        )
    else:
        msg4 = "No sudden spasm/jerk outliers detected in velocity profile."
    feedback["check_4_sudden_spasm_jerk"] = msg4
    print(f"4) {msg4}")

    print("=" * 70 + "\n")
    return feedback


def calculate_rom_metrics(ref_data: np.ndarray, pat_data: np.ndarray) -> Tuple[float, np.ndarray]:
    """
    Notebook logic:
      ref_range = np.ptp(ref_data, axis=0)
      pat_range = np.ptp(pat_data, axis=0)
      ref_range[ref_range == 0] = 1e-6
      ratios = pat_range / ref_range
      avg_rom_ratio = np.mean(ratios)
    """
    ref_range = np.ptp(ref_data, axis=0).astype(float)
    pat_range = np.ptp(pat_data, axis=0).astype(float)

    ref_range[ref_range == 0] = 1e-6

    ratios = pat_range / ref_range
    avg_rom_ratio = float(np.mean(ratios))
    return avg_rom_ratio, ratios


def get_rom_grade(ratio: float) -> int:
    """
    Relaxed ROM thresholds (doubled tolerance bands):
    - if ratio < 0.30 or ratio > 1.80: 0
    - if 0.90 <= ratio <= 1.10: 10
    - if ratio < 0.90:
        if ratio >= 0.80: 9
        elif ratio >= 0.60: 8
        else: 7
    - if ratio > 1.10:
        if ratio <= 1.20: 9
        elif ratio <= 1.50: 8
        else: 7
    """
    if ratio < 0.30 or ratio > 1.80:
        return 0

    if 0.90 <= ratio <= 1.10:
        return 10

    if ratio < 0.90:
        if ratio >= 0.80:
            return 9
        elif ratio >= 0.60:
            return 8
        else:
            return 7

    if ratio > 1.10:
        if ratio <= 1.20:
            return 9
        elif ratio <= 1.50:
            return 8
        else:
            return 7

    return 0


def get_shape_grade(rmse: float, limit: float) -> int:
    """
    Map mDTW global RMSE directly to 0-10 grade based on calibration mapping.
    (Note: 'limit' argument kept for interface compatibility).
    Thresholds doubled from original for relaxed scoring.
    """
    if rmse <= 0.08: return 10
    elif rmse <= 0.16: return 9
    elif rmse <= 0.24: return 8
    elif rmse <= 0.32: return 7
    elif rmse <= 0.40: return 6
    elif rmse <= 0.46: return 5
    elif rmse <= 0.60: return 4
    elif rmse <= 0.80: return 3
    elif rmse <= 1.06: return 2
    elif rmse <= 1.52: return 1
    else: return 0


def grade_tremor(pat_high: float, ref_high: float) -> int:
    """Grade tremor from high-band SPARC difference (0-10, hardcoded mapping).
    Thresholds tripled from original for relaxed scoring."""
    diff = abs(pat_high - ref_high)
    if diff <= 0.12: return 10
    elif diff <= 0.40: return 9
    elif diff <= 0.75: return 8
    elif diff <= 1.10: return 7
    elif diff <= 1.50: return 6
    elif diff <= 2.00: return 5
    elif diff <= 2.70: return 4
    elif diff <= 3.50: return 3
    elif diff <= 5.00: return 2
    elif diff <= 8.00: return 1
    else: return 0


def grade_hesitation(pat_low: float, ref_low: float) -> int:
    """Grade hesitation from low-band SPARC difference (0-10, hardcoded mapping).
    Thresholds doubled from original for relaxed scoring."""
    diff = abs(pat_low - ref_low)
    if diff <= 0.08: return 10
    elif diff <= 0.28: return 9
    elif diff <= 0.50: return 8
    elif diff <= 0.74: return 7
    elif diff <= 1.04: return 6
    elif diff <= 1.38: return 5
    elif diff <= 1.82: return 4
    elif diff <= 2.40: return 3
    elif diff <= 3.30: return 2
    elif diff <= 5.20: return 1
    else: return 0


def grade_tempo_control(vel_rmse: float) -> int:
    """Grade tempo and control purely from velocity profile RMSE (0-10, hardcoded mapping).
    Thresholds significantly relaxed — original scale was too strict for live capture variance."""
    if vel_rmse <= 0.05: return 10
    elif vel_rmse <= 0.15: return 9
    elif vel_rmse <= 0.30: return 8
    elif vel_rmse <= 0.50: return 7
    elif vel_rmse <= 0.75: return 6
    elif vel_rmse <= 1.00: return 5
    elif vel_rmse <= 1.50: return 4
    elif vel_rmse <= 2.00: return 3
    elif vel_rmse <= 3.00: return 2
    elif vel_rmse <= 5.00: return 1
    else: return 0


def build_patient_commentary(
    tremor_g: int, hesitation_g: int,
    tempo_control_g: int, rom_g: int, som_g: int,
    patient_feedback: dict,
) -> list:
    """Build patient-facing commentary list from grades and feedback."""
    msgs = []
    if tremor_g < 5:
        msgs.append("⚠ Too many tremors detected in your movement")
    if hesitation_g < 5:
        msgs.append("⚠ Too much hesitation — try to move more smoothly")
    if tempo_control_g < 5:
        msgs.append("⚠ Movement tempo/control doesn't match the reference pattern")
    if rom_g < 5:
        msgs.append("⚠ Range of motion needs improvement")
    if som_g < 5:
        msgs.append("⚠ Movement shape doesn't match the reference well")
    # Add velocity lead/lag feedback
    vel_fb = patient_feedback.get("check_3_velocity_lead_lag", "")
    if "too slow" in vel_fb.lower():
        msgs.append("⚠ You are performing too slow")
    elif "too fast" in vel_fb.lower():
        msgs.append("⚠ You are performing too fast")
    if not msgs:
        msgs.append("✓ Great performance! Keep it up.")
    return msgs


def calculate_mdtw_with_sensitivity(
    template: np.ndarray,
    query: np.ndarray,
    sensitivity: float = SENSITIVITY_DEFAULT,
    radius: int = DTW_RADIUS_DEFAULT,
) -> Tuple[
    float,  # final_score
    float,  # global_rmse
    Tuple[float, float, float],  # (rmse_x, rmse_y, rmse_z)
    np.ndarray,  # template_centered
    np.ndarray,  # query_centered
]:
    """
    Notebook logic (ported to be identical in computation):
    1) Mean-Centering ONLY
    2) Compute mDTW path
    3) Calculate Global RMSE
    4) Calculate Per-Axis RMSE along warped path
    5) Final score: 10.0 * exp(-sensitivity * global_rmse)
    """
    try:
        from tslearn.metrics import dtw_path
    except ImportError as e:
        raise ImportError(
            "tslearn is required to exactly match Scoring_Module/disected_mmDTW.ipynb. "
            "Install it (e.g. `pip install tslearn`) and re-run."
        ) from e

    # 1. Mean-Centering ONLY
    template_centered = template - np.mean(template, axis=0)
    query_centered = query - np.mean(query, axis=0)

    # 2. Compute mDTW path (exact call signature)
    optimal_path, sim_dist = dtw_path(
        template_centered,
        query_centered,
        global_constraint="sakoe_chiba",
        sakoe_chiba_radius=radius,
    )

    # 3. Calculate Global RMSE (3D)
    path_length = len(optimal_path)
    global_rmse = sim_dist / np.sqrt(path_length)

    # 4. Calculate Per-Axis RMSE along warped path
    sq_err_x, sq_err_y, sq_err_z = 0.0, 0.0, 0.0
    for (i, j) in optimal_path:
        sq_err_x += (template_centered[i, 0] - query_centered[j, 0]) ** 2
        sq_err_y += (template_centered[i, 1] - query_centered[j, 1]) ** 2
        sq_err_z += (template_centered[i, 2] - query_centered[j, 2]) ** 2

    rmse_x = np.sqrt(sq_err_x / path_length)
    rmse_y = np.sqrt(sq_err_y / path_length)
    rmse_z = np.sqrt(sq_err_z / path_length)

    # 5. Final Score (Patient Gamification View - Exponential Decay)
    final_score = 10.0 * np.exp(-sensitivity * global_rmse)
    return (
        round(float(final_score), 2),
        float(global_rmse),
        (float(rmse_x), float(rmse_y), float(rmse_z)),
        template_centered,
        query_centered,
    )


def generate_therapist_report(
    rom_ratio: float,
    avg_rom_grade: int,
    rom_axis_grades: List[int],
    rom_ratios: np.ndarray,
    global_rmse: float,
    shape_grade: int,
    axis_rmse: Tuple[float, float, float],
    shape_limit: float,
) -> str:
    """
    Notebook report text formatting logic (verbatim).
    """
    report = []

    # 1. ROM Analysis
    report.append(f"RANGE OF MOTION (ROM)")
    report.append(f"  > Global Grade: {avg_rom_grade} / 10")
    report.append(f"  > Avg Ratio:    {rom_ratio*100:.1f}% of Reference")
    report.append(f"  > Axis Breakdown:")
    report.append(f"    X: {rom_ratios[0]*100:.0f}% (Grade: {rom_axis_grades[0]})")
    report.append(f"    Y: {rom_ratios[1]*100:.0f}% (Grade: {rom_axis_grades[1]})")
    report.append(f"    Z: {rom_ratios[2]*100:.0f}% (Grade: {rom_axis_grades[2]})")

    if avg_rom_grade == 0:
        if rom_ratio < 1.0:
            report.append("  > STATUS: CRITICAL FAIL (Too Small)")
        else:
            report.append("  > STATUS: CRITICAL FAIL (Too Large)")
    elif avg_rom_grade >= 9:
        report.append("  > STATUS: EXCELLENT ROM")
    elif rom_ratio < 0.90:
        report.append("  > STATUS: RESTRICTED (Too Small)")
    elif rom_ratio > 1.10:
        report.append("  > STATUS: EXCESSIVE (Too Large)")
    else:
        report.append("  > STATUS: ACCEPTABLE DEVIATION")

    # 2. Shape Analysis
    report.append(f"\nSHAPE QUALITY (RMSE)")
    report.append(f"  > Grade:        {shape_grade} / 10")
    report.append(f"  > Global Error: {global_rmse:.3f} m")
    report.append(f"  > Limit:        < {shape_limit:.3f} m")

    rmse_x, rmse_y, rmse_z = axis_rmse
    max_err = max(rmse_x, rmse_y, rmse_z)

    report.append(f"  > Axis Error Breakdown:")
    report.append(f"    X: {rmse_x:.3f}m")
    report.append(f"    Y: {rmse_y:.3f}m")
    report.append(f"    Z: {rmse_z:.3f}m")

    if shape_grade == 0:
        report.append("  > STATUS: INCORRECT SHAPE (Mismatch)")
        if max_err == rmse_x:
            report.append("    -> MAIN ISSUE: Horizontal Path (X)")
        elif max_err == rmse_y:
            report.append("    -> MAIN ISSUE: Vertical Path (Y)")
        else:
            report.append("    -> MAIN ISSUE: Depth Control (Z)")
    else:
        report.append("  > STATUS: GOOD SHAPE MATCH")

    return "\n".join(report)


def plot_comparison(
    ref_data: np.ndarray,
    pat_data: np.ndarray,
    score: float,
    report_text: str,
    output_dir: str,
    optimal_path: List[Tuple[int, int]],
) -> str:
    """
    Notebook-style plot logic with a saved output.
    """
    fig = plt.figure(figsize=(15, 8))

    # Plot 1: 3D Trajectory
    ax1 = fig.add_subplot(1, 2, 1, projection="3d")
    ax1.plot(ref_data[:, 0], ref_data[:, 1], ref_data[:, 2], "k--", label="Expert Ref (Centered)")
    ax1.plot(pat_data[:, 0], pat_data[:, 1], pat_data[:, 2], "r", linewidth=2, label="Patient (Centered)")
    ax1.set_title(f"Patient Performance: {score}/10", fontsize=16, fontweight="bold", color="blue")
    ax1.legend()
    ax1.set_xlabel("X")
    ax1.set_ylabel("Y")
    ax1.set_zlabel("Z")

    # Plot 2: Therapist Dashboard (Text)
    ax2 = fig.add_subplot(1, 2, 2)
    ax2.axis("off")
    ax2.text(0.05, 0.98, "THERAPIST ANALYTICS DASHBOARD", fontsize=12, fontweight="bold", va="top")
    ax2.text(0.05, 0.90, report_text, fontsize=10, family="monospace", va="top")

    # X/Y/Z axis variation panels
    ax_x = fig.add_axes([0.55, 0.05, 0.12, 0.25])
    ax_x.plot(ref_data[:, 0], "k--")
    ax_x.plot(pat_data[:, 0], "r")
    ax_x.set_title("X-Axis (Centered)")
    ax_x.axis("off")

    ax_y = fig.add_axes([0.69, 0.05, 0.12, 0.25])
    ax_y.plot(ref_data[:, 1], "k--")
    ax_y.plot(pat_data[:, 1], "r")
    ax_y.set_title("Y-Axis (Centered)")
    ax_y.axis("off")

    ax_z = fig.add_axes([0.83, 0.05, 0.12, 0.25])
    ax_z.plot(ref_data[:, 2], "k--")
    ax_z.plot(pat_data[:, 2], "r")
    ax_z.set_title("Z-Axis (Centered)")
    ax_z.axis("off")

    plt.tight_layout()
    plot_path = os.path.join(output_dir, "score_plot.png")
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)
    return plot_path


def plot_filtered_output(ref_data_global: np.ndarray, pat_data_filtered_global: np.ndarray, output_dir: str) -> str:
    """
    Score plot: 3D trajectory overlay + per-axis comparison
    of the filtered patient signal vs the scaled reference template.
    """
    fig = plt.figure(figsize=(16, 10))
    gs = fig.add_gridspec(3, 2, width_ratios=[1, 1.2], hspace=0.35, wspace=0.30)

    # ── Left: 3D trajectory overlay ────────────────────────────────────
    ax3d = fig.add_subplot(gs[:, 0], projection="3d")
    ax3d.plot(ref_data_global[:, 0], ref_data_global[:, 1], ref_data_global[:, 2],
              "k--", linewidth=1.5, label="Reference Template")
    ax3d.plot(pat_data_filtered_global[:, 0], pat_data_filtered_global[:, 1],
              pat_data_filtered_global[:, 2],
              "r", linewidth=1.5, label="Patient (Filtered)")
    ax3d.set_title("3D Trajectory Overlay", fontsize=12, fontweight="bold")
    ax3d.set_xlabel("X")
    ax3d.set_ylabel("Y")
    ax3d.set_zlabel("Z")
    ax3d.legend(fontsize=9)

    # ── Right: per-axis comparison ─────────────────────────────────────
    axis_names = ["X", "Y", "Z"]
    for i in range(3):
        ax = fig.add_subplot(gs[i, 1])
        ax.plot(ref_data_global[:, i], "k--", linewidth=1.2,
                label=f"Template {axis_names[i]}")
        ax.plot(pat_data_filtered_global[:, i], "r", linewidth=1.2,
                label=f"Patient Filtered {axis_names[i]}")
        ax.set_ylabel("Position (m)")
        ax.set_title(f"{axis_names[i]} axis", fontsize=10, fontweight="bold")
        ax.legend(loc="best", fontsize=8)
        if i == 2:
            ax.set_xlabel("Frame")

    fig.suptitle("Score Plot: Filtered Signal vs Reference Template",
                 fontsize=14, fontweight="bold", y=0.98)
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out_path = os.path.join(output_dir, "score_plot.png")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_patient_view(
    global_score: float, dtw_score: float,
    som_grade: int, rom_grade: int,
    tempo_control_grade: float,
    hesitation_grade: float, tremor_grade: float,
    commentary: list, output_dir: str,
) -> str:
    """Patient-facing score report: hierarchical scores + feedback."""
    fig = plt.figure(figsize=(12, 10))
    gs = fig.add_gridspec(3, 2, hspace=0.45, wspace=0.35,
                          height_ratios=[1, 1.5, 1.2])

    # ── Row 0: Global Score ────────────────────────────────────────
    ax_title = fig.add_subplot(gs[0, :])
    ax_title.axis("off")
    ax_title.text(0.5, 0.78, "PATIENT SCORE REPORT",
                  ha="center", va="center", fontsize=20, fontweight="bold")
    color = "#2ecc71" if global_score >= 7 else "#f39c12" if global_score >= 4 else "#e74c3c"
    ax_title.text(0.5, 0.25, f"{global_score:.1f} / 10",
                  ha="center", va="center", fontsize=44, fontweight="bold", color=color)

    # ── Row 1 left: DTW sub-scores ─────────────────────────────────
    ax_dtw = fig.add_subplot(gs[1, 0])
    labels_d = ["Shape (SoM)", "Range (ROM)"]
    scores_d = [som_grade, rom_grade]
    colors_d = ["#3498db" if s >= 7 else "#f39c12" if s >= 4 else "#e74c3c" for s in scores_d]
    bars = ax_dtw.barh(labels_d, scores_d, color=colors_d, height=0.5)
    ax_dtw.set_xlim(0, 11)
    ax_dtw.set_title(f"DTW Score: {dtw_score:.1f} / 10", fontsize=13, fontweight="bold")
    for bar, sc in zip(bars, scores_d):
        ax_dtw.text(bar.get_width() + 0.2, bar.get_y() + bar.get_height() / 2,
                    f"{sc}/10", va="center", fontsize=11, fontweight="bold")

    # ── Row 1 right: Smoothness & Tempo sub-scores ──────────────────────────────
    ax_sp = fig.add_subplot(gs[1, 1])
    labels_s = ["Tempo & Control", "Hesitation", "Tremor"]
    scores_s = [tempo_control_grade, hesitation_grade, tremor_grade]
    colors_s = ["#3498db" if s >= 7 else "#f39c12" if s >= 4 else "#e74c3c" for s in scores_s]
    bars = ax_sp.barh(labels_s, scores_s, color=colors_s, height=0.5)
    ax_sp.set_xlim(0, 11)
    ax_sp.set_title("Movement Smoothness & Tempo", fontsize=13, fontweight="bold")
    for bar, sc in zip(bars, scores_s):
        ax_sp.text(bar.get_width() + 0.2, bar.get_y() + bar.get_height() / 2,
                    f"{sc:.1f}/10", va="center", fontsize=11, fontweight="bold")

    # ── Row 2: Feedback commentary ─────────────────────────────────
    ax_fb = fig.add_subplot(gs[2, :])
    ax_fb.axis("off")
    ax_fb.text(0.05, 0.95, "FEEDBACK", fontsize=14, fontweight="bold", va="top")
    feedback_text = "\n".join(commentary)
    ax_fb.text(0.05, 0.75, feedback_text, fontsize=11, va="top",
               family="sans-serif", linespacing=1.8)

    plt.tight_layout()
    path = os.path.join(output_dir, "patient_view.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_therapist_view(
    template_centered: np.ndarray, query_centered: np.ndarray,
    ref_data_global: np.ndarray, pat_data_global: np.ndarray,
    ref_speed: np.ndarray, pat_speed: np.ndarray,
    global_score: float, dtw_score: float,
    som_grade: int, rom_grade: int,
    tempo_control_grade: float,
    hesitation_grade: float, tremor_grade: float,
    raw_metrics_text: str, output_dir: str,
) -> str:
    """Therapist-facing analytics report: plots + raw data + scores."""
    fig = plt.figure(figsize=(20, 16))
    gs = fig.add_gridspec(3, 6, hspace=0.40, wspace=0.45)

    # ── Row 0 left: 3D trajectory (3 cols) ─────────────────────────
    ax3d = fig.add_subplot(gs[0, :3], projection="3d")
    ax3d.plot(template_centered[:, 0], template_centered[:, 1], template_centered[:, 2],
              "k--", label="Reference (Centered)")
    ax3d.plot(query_centered[:, 0], query_centered[:, 1], query_centered[:, 2],
              "r", linewidth=2, label="Patient (Centered)")
    ax3d.set_title(f"3D Trajectory  |  Global: {global_score}/10",
                   fontsize=13, fontweight="bold", color="blue")
    ax3d.legend(fontsize=8)
    ax3d.set_xlabel("X"); ax3d.set_ylabel("Y"); ax3d.set_zlabel("Z")

    # ── Row 0 right: Raw metrics text (3 cols) ─────────────────────
    ax_txt = fig.add_subplot(gs[0, 3:])
    ax_txt.axis("off")
    ax_txt.text(0.02, 0.98, "THERAPIST ANALYTICS DASHBOARD",
                fontsize=13, fontweight="bold", va="top")
    ax_txt.text(0.02, 0.88, raw_metrics_text,
                fontsize=8.5, family="monospace", va="top", linespacing=1.3)

    # ── Row 1: Per-axis traces (2 cols each) ───────────────────────
    axis_names = ["X", "Y", "Z"]
    for i in range(3):
        ax = fig.add_subplot(gs[1, i * 2:(i + 1) * 2])
        ax.plot(ref_data_global[:, i], "k--", linewidth=1.2, label="Template")
        ax.plot(pat_data_global[:, i], "r", linewidth=1.2, label="Patient")
        ax.set_title(f"{axis_names[i]}-Axis (Global)", fontsize=10, fontweight="bold")
        ax.set_ylabel("Position (m)")
        ax.set_xlabel("Frame")
        ax.legend(fontsize=7)

    # ── Row 2 left: Velocity profile (3 cols) ──────────────────────
    ax_vel = fig.add_subplot(gs[2, :3])
    ax_vel.plot(ref_speed, "k--", linewidth=1.2, label="Reference Speed")
    ax_vel.plot(pat_speed, "r", linewidth=1.2, label="Patient Speed")
    ax_vel.set_title("Velocity Profile", fontsize=11, fontweight="bold")
    ax_vel.set_ylabel("Speed (m/s)")
    ax_vel.set_xlabel("Frame")
    ax_vel.legend(fontsize=8)

    # ── Row 2 right: Score hierarchy bars (3 cols) ─────────────────
    ax_bar = fig.add_subplot(gs[2, 3:])
    all_labels = ["Global", "DTW", "  SoM", "  ROM",
                  "  Tempo/Ctrl", "  Hesit.", "  Tremor"]
    all_scores = [global_score, dtw_score, som_grade, rom_grade,
                  tempo_control_grade,
                  hesitation_grade, tremor_grade]
    bar_colors = []
    for idx, s in enumerate(all_scores):
        if idx in (0, 1):  # aggregate bars
            bar_colors.append("#2c3e50")
        elif s >= 7:
            bar_colors.append("#27ae60")
        elif s >= 4:
            bar_colors.append("#f39c12")
        else:
            bar_colors.append("#c0392b")
    y_pos = list(range(len(all_labels) - 1, -1, -1))
    ax_bar.barh(y_pos, all_scores, color=bar_colors, height=0.6)
    ax_bar.set_yticks(y_pos)
    ax_bar.set_yticklabels(all_labels, fontsize=9)
    ax_bar.set_xlim(0, 11)
    ax_bar.set_title("Score Hierarchy", fontsize=11, fontweight="bold")
    for yp, sc in zip(y_pos, all_scores):
        ax_bar.text(sc + 0.15, yp, f"{sc:.1f}" if isinstance(sc, float) else f"{sc}",
                    va="center", fontsize=9, fontweight="bold")

    plt.tight_layout()
    path = os.path.join(output_dir, "therapist_view.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def compute_score(
    patient_filtered_path: str,
    scaled_template_path: str,
    output_dir: str,
    sensitivity: float = SENSITIVITY_DEFAULT,
    radius: int = DTW_RADIUS_DEFAULT,
    shape_limit_m: float = SHAPE_TOLERANCE_M_DEFAULT,
    sparc_sample_rate: float = SAMPLE_RATE_DEFAULT,
    sparc_filter_freq: float = FILTER_FREQ_DEFAULT,
    sparc_threshold_pct: float = SPARC_THRESHOLD_PERCENT_DEFAULT,
    sparc_use_filtering: bool = ENABLE_SPARC_FILTERING_DEFAULT,
    jerk_iqr_multiplier: float = JERK_IQR_MULTIPLIER_DEFAULT,
    velocity_buffer_pct: float = VELOCITY_BUFFER_PCT_DEFAULT,
    weights: dict | None = None,
) -> Dict:
    """
    Computes the notebook-aligned score and writes outputs.

    Parameters
    ----------
    weights : dict | None
        Scoring weights dict. If None, loads from scoring_weights.json.
    """
    if not os.path.isfile(patient_filtered_path):
        raise FileNotFoundError(f"Patient file not found: {patient_filtered_path}")
    if not os.path.isfile(scaled_template_path):
        raise FileNotFoundError(f"Template file not found: {scaled_template_path}")

    os.makedirs(output_dir, exist_ok=True)

    # Load weights
    if weights is None:
        weights = load_weights()

    try:
        pat_df = pd.read_excel(patient_filtered_path)
    except Exception as e:
        raise RuntimeError(f"Failed to read patient Excel: {patient_filtered_path}. Error: {e}") from e

    try:
        ref_df = pd.read_excel(scaled_template_path)
    except Exception as e:
        raise RuntimeError(f"Failed to read template Excel: {scaled_template_path}. Error: {e}") from e

    _require_columns(ref_df, TEMPLATE_COLS, "Template")

    # Patient trajectory comes from 3-stage filtered output.
    pat_data, patient_trajectory_source = _extract_patient_global_trajectory_from_filtered(pat_df)
    ref_df = ref_df.dropna(subset=TEMPLATE_COLS)
    if len(ref_df) < 2:
        raise ValueError("Insufficient non-NaN rows in template data.")
    ref_data = ref_df[TEMPLATE_COLS].to_numpy(dtype=float)

    # 1) ROM metrics
    rom_ratio, rom_ratios = calculate_rom_metrics(ref_data, pat_data)

    # 2) Per-axis grades + global avg grade (rounded)
    rom_axis_grades = [get_rom_grade(float(r)) for r in rom_ratios]
    avg_rom_grade = int(round(np.mean(rom_axis_grades)))

    # 3) mDTW scoring
    try:
        from tslearn.metrics import dtw_path
    except ImportError as e:
        raise ImportError(
            "tslearn is required to exactly match Scoring_Module/disected_mmDTW.ipynb. "
            "Install it (e.g. `pip install tslearn`) and re-run."
        ) from e

    template_centered = ref_data - np.mean(ref_data, axis=0)
    query_centered = pat_data - np.mean(pat_data, axis=0)
    optimal_path, sim_dist = dtw_path(
        template_centered,
        query_centered,
        global_constraint="sakoe_chiba",
        sakoe_chiba_radius=radius,
    )

    path_length = len(optimal_path)
    global_rmse = sim_dist / np.sqrt(path_length)

    sq_err_x, sq_err_y, sq_err_z = 0.0, 0.0, 0.0
    for (i, j) in optimal_path:
        sq_err_x += (template_centered[i, 0] - query_centered[j, 0]) ** 2
        sq_err_y += (template_centered[i, 1] - query_centered[j, 1]) ** 2
        sq_err_z += (template_centered[i, 2] - query_centered[j, 2]) ** 2

    rmse_x = np.sqrt(sq_err_x / path_length)
    rmse_y = np.sqrt(sq_err_y / path_length)
    rmse_z = np.sqrt(sq_err_z / path_length)
    axis_rmse = (float(rmse_x), float(rmse_y), float(rmse_z))

    # 4) Shape grade
    shape_grade = get_shape_grade(global_rmse, shape_limit_m)

    # 5) Generate therapist report (notebook text formatting)
    report_text = generate_therapist_report(
        rom_ratio=rom_ratio,
        avg_rom_grade=avg_rom_grade,
        rom_axis_grades=rom_axis_grades,
        rom_ratios=rom_ratios,
        global_rmse=global_rmse,
        shape_grade=shape_grade,
        axis_rmse=axis_rmse,
        shape_limit=shape_limit_m,
    )

    # 5b) SPARC analysis (exact logic from disected_SPARC.ipynb)
    sparc_analyzer = MovementAnalyzer(
        fs=sparc_sample_rate,
        cutoff=sparc_filter_freq,
        jerk_iqr_multiplier=jerk_iqr_multiplier,
    )
    sparc_metrics = sparc_analyzer.compare_performances(
        ref_pos=ref_data,
        pat_pos=pat_data,
        use_filter=sparc_use_filtering,
    )
    sparc_status = print_clinical_report(sparc_metrics, sparc_threshold_pct)
    template_frame_count = len(ref_data)
    patient_feedback = print_patient_feedback(
        sparc_metrics,
        velocity_buffer_pct=velocity_buffer_pct,
        template_frame_count=template_frame_count,
    )

    # 6) Hierarchical grading (all out of 10)
    sparc_ref = sparc_metrics["Reference"]
    sparc_pat = sparc_metrics["Patient"]
    plot_data = sparc_metrics["Plot_Data"]

    som_grade = shape_grade                          # existing
    rom_grade_val = avg_rom_grade                    # existing

    tremor_g   = grade_tremor(sparc_pat["High_Band_SPARC"], sparc_ref["High_Band_SPARC"])
    hesit_g    = grade_hesitation(sparc_pat["Low_Band_SPARC"], sparc_ref["Low_Band_SPARC"])
    tempo_control_g = grade_tempo_control(sparc_pat["Velocity_RMSE"])

    # ── Combine weights for unified Tempo/Control ────────────────────
    weights["tempo_control"] = weights.get("velocity_profile", 0.1) + weights.get("control", 0.1)
    if "velocity_profile" in weights: del weights["velocity_profile"]
    if "control" in weights: del weights["control"]

    # ── Weighted scoring using configurable weights ────────────────────
    dtw_score = weighted_average(
        {"som": som_grade, "rom": rom_grade_val},
        weights,
    )
    global_score_10 = weighted_average(
        {"som": som_grade, "rom": rom_grade_val,
         "tempo_control": tempo_control_g,
         "hesitation": hesit_g, "tremor": tremor_g},
        weights,
    )

    commentary = build_patient_commentary(
        tremor_g, hesit_g, tempo_control_g, rom_grade_val, som_grade,
        patient_feedback,
    )

    print(f"\n--- Loading Data ---")
    print(f"Reference: {os.path.basename(scaled_template_path)}")
    print(f"Patient:   {os.path.basename(patient_filtered_path)}")
    print("=" * 50)
    print(f"PATIENT VIEW -> SCORE: {global_score_10}/10")
    print("=" * 50)
    print("THERAPIST VIEW -> ANALYTICS:")
    print(report_text)
    print("=" * 50)

    print("\n" + "=" * 60)
    print("  HIERARCHICAL SCORES (all out of 10) — WEIGHTED")
    print("=" * 60)
    print(f"  Weights: SoM={weights.get('som', 0)}, ROM={weights.get('rom', 0)}, "
          f"Tremor={weights.get('tremor', 0)}, Hesit={weights.get('hesitation', 0)}, "
          f"Tempo/Control={weights.get('tempo_control', 0)}")
    print(f"  Global Score:      {global_score_10} / 10")
    print(f"  +- DTW Score:      {dtw_score} / 10")
    print(f"  |  +- SoM:         {som_grade} / 10")
    print(f"  |  +- ROM:         {rom_grade_val} / 10")
    print(f"  +- Smoothness Metrics")
    print(f"     +- Tempo & Ctrl:{tempo_control_g} / 10")
    print(f"     +- Hesitation:  {hesit_g} / 10")
    print(f"     +- Tremor:      {tremor_g} / 10")
    print("=" * 60)

    # 7) Build therapist raw-metrics text
    raw_metrics_text = (
        f"HIERARCHICAL SCORES (WEIGHTED)\n"
        f"{'='*40}\n"
        f"Global Score:          {global_score_10} / 10\n"
        f"DTW Score:             {dtw_score} / 10\n"
        f"  Shape (SoM):         {som_grade} / 10  [RMSE: {global_rmse:.3f}m]\n"
        f"  Range (ROM):         {rom_grade_val} / 10  [Ratio: {rom_ratio*100:.1f}%]\n"
        f"Tempo & Control:       {tempo_control_g} / 10  [RMSE: {sparc_pat['Velocity_RMSE']:.3f}]\n"
        f"Hesitation:            {hesit_g} / 10  [dLow: {abs(sparc_pat['Low_Band_SPARC']-sparc_ref['Low_Band_SPARC']):.3f}]\n"
        f"Tremor:                {tremor_g} / 10  [dHigh: {abs(sparc_pat['High_Band_SPARC']-sparc_ref['High_Band_SPARC']):.3f}]\n"
        f"\n"
        f"RAW SPARC VALUES\n"
        f"{'='*40}\n"
        f"{'Metric':<20} {'REF':>10} {'PAT':>10}\n"
        f"{'Total SPARC':<20} {sparc_ref['Total_SPARC']:>10.4f} {sparc_pat['Total_SPARC']:>10.4f}\n"
        f"{'Low Band':<20} {sparc_ref['Low_Band_SPARC']:>10.4f} {sparc_pat['Low_Band_SPARC']:>10.4f}\n"
        f"{'High Band':<20} {sparc_ref['High_Band_SPARC']:>10.4f} {sparc_pat['High_Band_SPARC']:>10.4f}\n"
        f"{'Peak Velocity':<20} {sparc_ref['Peak_Velocity']:>10.4f} {sparc_pat['Peak_Velocity']:>10.4f}\n"
        f"{'Mean Velocity':<20} {sparc_ref['Mean_Velocity']:>10.4f} {sparc_pat['Mean_Velocity']:>10.4f}\n"
        f"{'Vel RMSE':<20} {'--':>10} {sparc_pat['Velocity_RMSE']:>10.4f}\n"
        f"\n"
        f"ROM: {rom_ratio*100:.1f}% (X:{rom_ratios[0]*100:.0f}% Y:{rom_ratios[1]*100:.0f}% Z:{rom_ratios[2]*100:.0f}%)\n"
        f"Shape RMSE: {global_rmse:.3f}m  (X:{axis_rmse[0]:.3f} Y:{axis_rmse[1]:.3f} Z:{axis_rmse[2]:.3f})"
    )

    # 8) Save plots
    filtered_plot_path = plot_filtered_output(
        ref_data_global=ref_data,
        pat_data_filtered_global=pat_data,
        output_dir=output_dir,
    )
    patient_view_path = plot_patient_view(
        global_score=global_score_10, dtw_score=dtw_score,
        som_grade=som_grade, rom_grade=rom_grade_val,
        tempo_control_grade=tempo_control_g,
        hesitation_grade=hesit_g, tremor_grade=tremor_g,
        commentary=commentary, output_dir=output_dir,
    )
    therapist_view_path = plot_therapist_view(
        template_centered=template_centered, query_centered=query_centered,
        ref_data_global=ref_data, pat_data_global=pat_data,
        ref_speed=plot_data["Ref_Speed"], pat_speed=plot_data["Pat_Speed"],
        global_score=global_score_10, dtw_score=dtw_score,
        som_grade=som_grade, rom_grade=rom_grade_val,
        tempo_control_grade=tempo_control_g,
        hesitation_grade=hesit_g, tremor_grade=tremor_g,
        raw_metrics_text=raw_metrics_text, output_dir=output_dir,
    )

    # 9) Save raw_scores.xlsx (raw metric values only)
    raw_scores_df = pd.DataFrame({
        "ref_total_sparc": [sparc_ref["Total_SPARC"]],
        "pat_total_sparc": [sparc_pat["Total_SPARC"]],
        "ref_low_band_sparc": [sparc_ref["Low_Band_SPARC"]],
        "pat_low_band_sparc": [sparc_pat["Low_Band_SPARC"]],
        "ref_high_band_sparc": [sparc_ref["High_Band_SPARC"]],
        "pat_high_band_sparc": [sparc_pat["High_Band_SPARC"]],
        "ref_peak_velocity": [sparc_ref["Peak_Velocity"]],
        "pat_peak_velocity": [sparc_pat["Peak_Velocity"]],
        "ref_mean_velocity": [sparc_ref["Mean_Velocity"]],
        "pat_mean_velocity": [sparc_pat["Mean_Velocity"]],
        "pat_velocity_rmse": [sparc_pat["Velocity_RMSE"]],
        "pat_sudden_peak_count": [sparc_pat["Sudden_Peak_Count"]],
        "pat_sudden_drop_count": [sparc_pat["Sudden_Drop_Count"]],
        "pat_velocity_lag_frames": [sparc_pat["Velocity_Peak_Lag_Frames"]],
        "pat_velocity_lag_seconds": [sparc_pat["Velocity_Peak_Lag_Seconds"]],
        "rom_ratio_avg": [rom_ratio],
        "rom_ratio_x": [rom_ratios[0]],
        "rom_ratio_y": [rom_ratios[1]],
        "rom_ratio_z": [rom_ratios[2]],
        "global_rmse": [global_rmse],
        "rmse_x": [axis_rmse[0]],
        "rmse_y": [axis_rmse[1]],
        "rmse_z": [axis_rmse[2]],
    })
    raw_scores_path = os.path.join(output_dir, "raw_scores.xlsx")
    raw_scores_df.to_excel(raw_scores_path, index=False, engine="openpyxl")

    # 10) Save score_results.xlsx (hierarchical grades + raw + config)
    alignment_df = pd.DataFrame({
        "template_index": [i for (i, _) in optimal_path],
        "query_index": [j for (_, j) in optimal_path],
    })

    scores_df = pd.DataFrame({
        "global_score": [global_score_10],
        "dtw_score": [dtw_score],
        "som_grade": [som_grade],
        "rom_grade": [rom_grade_val],
        "tempo_control_grade": [tempo_control_g],
        "hesitation_grade": [hesit_g],
        "tremor_grade": [tremor_g],
        "global_rmse": [global_rmse],
        "rmse_x": [axis_rmse[0]],
        "rmse_y": [axis_rmse[1]],
        "rmse_z": [axis_rmse[2]],
        "rom_ratio_avg": [rom_ratio],
        "rom_ratio_x": [rom_ratios[0]],
        "rom_ratio_y": [rom_ratios[1]],
        "rom_ratio_z": [rom_ratios[2]],
        "rom_grade_x": [rom_axis_grades[0]],
        "rom_grade_y": [rom_axis_grades[1]],
        "rom_grade_z": [rom_axis_grades[2]],
        "sensitivity": [sensitivity],
        "dtw_radius": [radius],
        "shape_limit_m": [shape_limit_m],
        "patient_trajectory_source": [patient_trajectory_source],
        "weight_som": [weights.get("som", 0)],
        "weight_rom": [weights.get("rom", 0)],
        "weight_tremor": [weights.get("tremor", 0)],
        "weight_hesitation": [weights.get("hesitation", 0)],
        "weight_tempo_control": [weights.get("tempo_control", 0)],
    })

    sparc_df = pd.DataFrame({
        "ref_total_sparc": [sparc_ref["Total_SPARC"]],
        "pat_total_sparc": [sparc_pat["Total_SPARC"]],
        "ref_low_band_sparc": [sparc_ref["Low_Band_SPARC"]],
        "pat_low_band_sparc": [sparc_pat["Low_Band_SPARC"]],
        "ref_high_band_sparc": [sparc_ref["High_Band_SPARC"]],
        "pat_high_band_sparc": [sparc_pat["High_Band_SPARC"]],
        "ref_peak_velocity": [sparc_ref["Peak_Velocity"]],
        "pat_peak_velocity": [sparc_pat["Peak_Velocity"]],
        "ref_mean_velocity": [sparc_ref["Mean_Velocity"]],
        "pat_velocity_rmse": [sparc_pat["Velocity_RMSE"]],
        "status_overall_sparc": [sparc_status.get("Overall SPARC", "")],
        "status_choppiness": [sparc_status.get("Choppiness (0-5Hz)", "")],
        "status_tremor": [sparc_status.get("Tremor (5-20Hz)", "")],
        "status_velocity_rmse": [sparc_status.get("Velocity RMSE", "")],
        "status_peak_velocity": [sparc_status.get("Peak Velocity", "")],
    })

    feedback_df = pd.DataFrame({
        "commentary": ["\n".join(commentary)],
        "feedback_choppy_0_5hz": [patient_feedback.get("check_1_choppy_0_5hz", "")],
        "feedback_shaking_6_20hz": [patient_feedback.get("check_2_shaking_6_20hz", "")],
        "feedback_velocity_lead_lag": [patient_feedback.get("check_3_velocity_lead_lag", "")],
        "feedback_sudden_spasm_jerk": [patient_feedback.get("check_4_sudden_spasm_jerk", "")],
    })

    report_df = pd.DataFrame({"report_text": [report_text]})

    results_xlsx_path = os.path.join(output_dir, "score_results.xlsx")
    with pd.ExcelWriter(results_xlsx_path, engine="openpyxl") as writer:
        scores_df.to_excel(writer, sheet_name="scores", index=False)
        sparc_df.to_excel(writer, sheet_name="sparc_raw", index=False)
        feedback_df.to_excel(writer, sheet_name="feedback", index=False)
        alignment_df.to_excel(writer, sheet_name="dtw_alignment", index=False)
        report_df.to_excel(writer, sheet_name="report", index=False)

    print(f"[OK] Saved score results:    {results_xlsx_path}")
    print(f"[OK] Saved raw scores:       {raw_scores_path}")
    print(f"[OK] Saved patient view:     {patient_view_path}")
    print(f"[OK] Saved therapist view:   {therapist_view_path}")
    print(f"[OK] Saved score plot:       {filtered_plot_path}")

    return {
        "global_score": global_score_10,
        "dtw_score": dtw_score,
        "som_grade": som_grade,
        "rom_grade": rom_grade_val,
        "tempo_control_grade": tempo_control_g,
        "hesitation_grade": hesit_g,
        "tremor_grade": tremor_g,
        "global_rmse": float(global_rmse),
        "axis_rmse": {"X": axis_rmse[0], "Y": axis_rmse[1], "Z": axis_rmse[2]},
        "rom_ratio_avg": float(rom_ratio),
        "rom_ratios": {"X": float(rom_ratios[0]), "Y": float(rom_ratios[1]), "Z": float(rom_ratios[2])},
        "rom_axis_grades": {"X": rom_axis_grades[0], "Y": rom_axis_grades[1], "Z": rom_axis_grades[2]},
        "report_text": report_text,
        "commentary": commentary,
        "sparc": {
            "reference": sparc_ref,
            "patient": sparc_pat,
            "status": sparc_status,
            "patient_feedback": patient_feedback,
        },
        "weights_used": weights,
        "saved": {
            "results_excel": results_xlsx_path,
            "raw_scores_excel": raw_scores_path,
            "patient_view_png": patient_view_path,
            "therapist_view_png": therapist_view_path,
            "score_plot_png": filtered_plot_path,
        },
    }


def score_movement(
    patient_filtered_path: str,
    template_scaled_path: str,
    output_dir: str,
    velocity_buffer_pct: float = VELOCITY_BUFFER_PCT_DEFAULT,
    weights: dict | None = None,
) -> Dict:
    """
    Compatibility wrapper expected by `main_pipeline.py`.
    """
    return compute_score(
        patient_filtered_path=patient_filtered_path,
        scaled_template_path=template_scaled_path,
        output_dir=output_dir,
        velocity_buffer_pct=velocity_buffer_pct,
        weights=weights,
    )
