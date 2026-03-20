"""
disected_mmDTW.py — PhysioSync Analysis Engine
================================================
Combined DTW + SPARC analysis module.

Exports used by server.py:
  - calculate_mdtw_with_sensitivity(template, query, sensitivity, radius)
      returns: score, global_rmse, axis_rmse, template_centered, query_centered
  - calculate_rom_metrics(ref_data, pat_data)
  - get_rom_grade(ratio)
  - get_shape_grade(rmse, limit)
  - generate_therapist_report(...)
  - MovementAnalyzer  (SPARC class)
  - get_sparc_grade(pat_sparc, ref_sparc, threshold_pct)
"""

import numpy as np
import pandas as pd
import os
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from tslearn.metrics import dtw_path
from scipy.fft import fft, fftfreq
from scipy.signal import butter, filtfilt, resample


# =============================================================================
# DATA EXTRACTION
# =============================================================================

def add_awgn(data, std_dev=0.01):
    """Adds Additive White Gaussian Noise to simulate tremor/sensor jitter."""
    noise = np.random.normal(0, std_dev, data.shape)
    return data + noise

def get_arm_side(filename):
    if "(L)" in filename.upper(): return "Left"
    return "Right"

def extract_hand_data(file_path):
    """Extracts Hand x, y, z data — tries multiple column naming conventions."""
    side = get_arm_side(os.path.basename(file_path))
    try:
        df = pd.read_excel(file_path)
    except:
        df = pd.read_csv(file_path)

    possible_cols = [
        [f"{side} Hand x", f"{side} Hand y", f"{side} Hand z"],  # Xsens
        ["Wrist_x", "Wrist_y", "Wrist_z"],                        # MediaPipe / shoulder_origin
        ["wrist_scaled_x", "wrist_scaled_y", "wrist_scaled_z"],   # Scaled template
        ["x", "y", "z"],                                           # Generic
    ]
    for cols in possible_cols:
        if all(c in df.columns for c in cols):
            return df[cols].dropna().values

    raise KeyError(f"Could not find valid X,Y,Z columns in {os.path.basename(file_path)}")


# =============================================================================
# DTW — CORE ALGORITHMS
# =============================================================================

def calculate_rom_metrics(ref_data, pat_data):
    """Computes Range of Motion (ROM) metrics."""
    ref_range = np.ptp(ref_data, axis=0)
    pat_range = np.ptp(pat_data, axis=0)
    ref_range[ref_range == 0] = 1e-6
    ratios = pat_range / ref_range
    avg_rom_ratio = np.mean(ratios)
    return avg_rom_ratio, ratios

def get_rom_grade(ratio):
    """Maps a single ROM ratio to a 0 / 7-10 clinical grade."""
    if ratio < 0.50 or ratio > 1.50: return 0
    if 0.95 <= ratio <= 1.05:         return 10
    if ratio < 0.95:
        if ratio >= 0.90:  return 9
        elif ratio >= 0.70: return 8
        else:               return 7
    if ratio > 1.05:
        if ratio <= 1.10:  return 9
        elif ratio <= 1.30: return 8
        else:               return 7

def get_shape_grade(rmse, limit):
    """Maps RMSE to a 0 / 6-10 clinical grade."""
    if rmse > limit: return 0
    step = limit / 5.0
    if rmse <= step:        return 10
    elif rmse <= step * 2:  return 9
    elif rmse <= step * 3:  return 8
    elif rmse <= step * 4:  return 7
    else:                   return 6

def calculate_mdtw_with_sensitivity(template, query, sensitivity=3.0, radius=10):
    """
    Computes multivariate DTW metrics.

    Returns
    -------
    score            : float  — 0–100 patient-facing gamification score
    global_rmse      : float  — 3D RMSE in meters
    axis_rmse        : tuple  — (rmse_x, rmse_y, rmse_z)
    template_centered: ndarray — mean-centred template (for plotting)
    query_centered   : ndarray — mean-centred patient path (for plotting)
    """
    template_centered = template - np.mean(template, axis=0)
    query_centered    = query    - np.mean(query,    axis=0)

    optimal_path, sim_dist = dtw_path(
        template_centered, query_centered,
        global_constraint="sakoe_chiba",
        sakoe_chiba_radius=radius,
    )

    path_length = len(optimal_path)
    global_rmse = sim_dist / np.sqrt(path_length)

    sq_err_x = sq_err_y = sq_err_z = 0.0
    for (i, j) in optimal_path:
        sq_err_x += (template_centered[i, 0] - query_centered[j, 0]) ** 2
        sq_err_y += (template_centered[i, 1] - query_centered[j, 1]) ** 2
        sq_err_z += (template_centered[i, 2] - query_centered[j, 2]) ** 2

    rmse_x = np.sqrt(sq_err_x / path_length)
    rmse_y = np.sqrt(sq_err_y / path_length)
    rmse_z = np.sqrt(sq_err_z / path_length)

    final_score = 100 * np.exp(-sensitivity * global_rmse)

    return round(final_score, 2), global_rmse, (rmse_x, rmse_y, rmse_z), template_centered, query_centered


# =============================================================================
# SPARC — MOVEMENT SMOOTHNESS ANALYSIS
# =============================================================================

class MovementAnalyzer:
    """
    Computes SPARC (Spectral Arc Length) smoothness metrics.

    Frequency bands (from literature):
      Low  0–5 Hz   → movement choppiness / shape
      High 5–20 Hz  → tremor / involuntary oscillation
    """

    FREQ_LIMIT_LOW  = 5.0
    FREQ_LIMIT_HIGH = 20.0

    def __init__(self, fs=30.0, cutoff=14.0):
        self.fs     = fs
        self.cutoff = cutoff

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _low_pass_filter(self, data):
        """4th-order Butterworth low-pass filter to remove camera jitter."""
        nyq = 0.5 * self.fs
        normal_cutoff = self.cutoff / nyq
        b, a = butter(4, normal_cutoff, btype='low', analog=False)
        filtered = np.zeros_like(data)
        for i in range(data.shape[1]):
            filtered[:, i] = filtfilt(b, a, data[:, i])
        return filtered

    def _get_speed_profile(self, positions):
        """Euclidean speed from 3-D positions (m/s)."""
        velocity = np.diff(positions, axis=0) * self.fs
        velocity = np.vstack([np.zeros(3), velocity])
        return np.sqrt(np.sum(velocity ** 2, axis=1))

    def _get_spectrum(self, speed_profile):
        """Returns (freqs, normalised_magnitude) for positive frequencies."""
        N = len(speed_profile)
        n_fft = int(2 ** (np.ceil(np.log2(N)) + 4))   # zero-pad by 4 octaves
        spectrum = np.abs(fft(speed_profile, n_fft))
        dc = spectrum[0] if spectrum[0] != 0 else 1.0
        norm_spec = spectrum / dc
        freqs = fftfreq(n_fft, d=1.0 / self.fs)
        mask  = freqs >= 0
        return freqs[mask], norm_spec[mask]

    def _arc_length(self, freqs, norm_spec):
        """Spectral arc-length (negative = more negative → smoother)."""
        if len(freqs) < 2:
            return 0.0
        df = freqs[1] - freqs[0]
        ds = np.diff(norm_spec)
        wc = 40 * np.pi          # normalisation constant (20 Hz)
        integrand = np.sqrt((1.0 / wc) ** 2 + (ds / df) ** 2)
        return -np.sum(integrand) * df

    # ── Public API ────────────────────────────────────────────────────────────

    def calculate_sparc_components(self, speed_profile):
        """
        Returns (sparc_total, sparc_low, sparc_high).
        More negative = more complex / less smooth.
        """
        freqs, norm_spec = self._get_spectrum(speed_profile)

        mask_total = freqs <= self.FREQ_LIMIT_HIGH
        mask_low   = freqs <= self.FREQ_LIMIT_LOW
        mask_high  = (freqs > self.FREQ_LIMIT_LOW) & (freqs <= self.FREQ_LIMIT_HIGH)

        sparc_total = self._arc_length(freqs[mask_total], norm_spec[mask_total])
        sparc_low   = self._arc_length(freqs[mask_low],   norm_spec[mask_low])
        sparc_high  = self._arc_length(freqs[mask_high],  norm_spec[mask_high])

        return sparc_total, sparc_low, sparc_high

    def compare_performances(self, ref_pos, pat_pos, use_filter=True):
        """
        Full SPARC comparison between reference and patient positions.

        Parameters
        ----------
        ref_pos, pat_pos : ndarray  shape (N, 3)
        use_filter       : bool     apply low-pass filter before analysis

        Returns
        -------
        dict with keys: Reference, Patient, Plot_Data
        """
        ref_final = self._low_pass_filter(ref_pos) if use_filter else ref_pos.copy()
        pat_final = self._low_pass_filter(pat_pos) if use_filter else pat_pos.copy()

        ref_speed = self._get_speed_profile(ref_final)
        pat_speed = self._get_speed_profile(pat_final)

        ref_sparc, ref_low, ref_high = self.calculate_sparc_components(ref_speed)
        pat_sparc, pat_low, pat_high = self.calculate_sparc_components(pat_speed)

        pat_speed_rs = resample(pat_speed, len(ref_speed))
        vel_rmse     = float(np.sqrt(np.mean((ref_speed - pat_speed_rs) ** 2)))

        return {
            "Reference": {
                "Total_SPARC":    ref_sparc,
                "Low_Band_SPARC": ref_low,
                "High_Band_SPARC": ref_high,
                "Peak_Velocity":  float(np.max(ref_speed)),
                "Mean_Velocity":  float(np.mean(ref_speed)),
            },
            "Patient": {
                "Total_SPARC":    pat_sparc,
                "Low_Band_SPARC": pat_low,
                "High_Band_SPARC": pat_high,
                "Peak_Velocity":  float(np.max(pat_speed)),
                "Velocity_RMSE":  vel_rmse,
            },
            "Plot_Data": {
                "Ref_Speed": ref_speed,
                "Pat_Speed": pat_speed,
                "Ref_Pos":   ref_final,
                "Pat_Pos":   pat_final,
            },
        }

    def get_spectrum_for_plot(self, speed_profile):
        """Public wrapper for plotting."""
        return self._get_spectrum(speed_profile)


def get_sparc_grade(pat_sparc, ref_sparc, threshold_pct=0.30):
    """
    Maps SPARC comparison to a 0–10 grade.

    SPARC values are negative; closer to 0 = less smooth.
    Patient must stay within threshold_pct of the reference.

    Grade logic:
      ratio = pat / ref   (both negative → ratio > 1 means patient is smoother)
      ratio >= 1.0        → 10  (at least as smooth as expert)
      ratio >= 0.95       → 9
      ratio >= 0.85       → 8
      ratio >= 0.70       → 7
      ratio >= 0.50       → 6
      below 0.50          → 0  (fail — very jerky / choppy)
    """
    if ref_sparc == 0:
        return 0
    ratio = pat_sparc / ref_sparc  # both negative → higher ratio = smoother patient
    if ratio >= 1.00: return 10
    if ratio >= 0.95: return 9
    if ratio >= 0.85: return 8
    if ratio >= 0.70: return 7
    if ratio >= 0.50: return 6
    return 0


# =============================================================================
# REPORTING
# =============================================================================

def generate_therapist_report(
    rom_ratio, avg_rom_grade, rom_axis_grades, rom_ratios,
    global_rmse, shape_grade, axis_rmse, shape_limit,
    sparc_metrics=None,
):
    """
    Generates combined DTW + SPARC therapist report text.
    sparc_metrics is the dict returned by MovementAnalyzer.compare_performances()
    (pass None to omit the SPARC section — backwards compatible).
    """
    report = []

    # ── 1. ROM ────────────────────────────────────────────────────────────────
    report.append("RANGE OF MOTION (ROM)")
    report.append(f"  > Global Grade: {avg_rom_grade} / 10")
    report.append(f"  > Avg Ratio:    {rom_ratio*100:.1f}% of Reference")
    report.append(f"  > Axis Breakdown:")
    report.append(f"    X: {rom_ratios[0]*100:.0f}% (Grade: {rom_axis_grades[0]})")
    report.append(f"    Y: {rom_ratios[1]*100:.0f}% (Grade: {rom_axis_grades[1]})")
    report.append(f"    Z: {rom_ratios[2]*100:.0f}% (Grade: {rom_axis_grades[2]})")
    if avg_rom_grade == 0:
        report.append("  > STATUS: CRITICAL FAIL (Too Small)" if rom_ratio < 1.0
                      else "  > STATUS: CRITICAL FAIL (Too Large)")
    elif avg_rom_grade >= 9:
        report.append("  > STATUS: EXCELLENT ROM")
    elif rom_ratio < 0.90:
        report.append("  > STATUS: RESTRICTED (Too Small)")
    elif rom_ratio > 1.10:
        report.append("  > STATUS: EXCESSIVE (Too Large)")
    else:
        report.append("  > STATUS: ACCEPTABLE DEVIATION")

    # ── 2. Shape ──────────────────────────────────────────────────────────────
    report.append("\nSHAPE QUALITY (DTW-RMSE)")
    report.append(f"  > Grade:        {shape_grade} / 10")
    report.append(f"  > Global Error: {global_rmse:.3f} m")
    report.append(f"  > Limit:        < {shape_limit:.3f} m")
    rmse_x, rmse_y, rmse_z = axis_rmse
    max_err = max(rmse_x, rmse_y, rmse_z)
    report.append(f"  > Axis Errors:  X={rmse_x:.3f}m  Y={rmse_y:.3f}m  Z={rmse_z:.3f}m")
    if shape_grade == 0:
        report.append("  > STATUS: INCORRECT SHAPE (Mismatch)")
        if   max_err == rmse_x: report.append("    -> MAIN ISSUE: Horizontal Path (X)")
        elif max_err == rmse_y: report.append("    -> MAIN ISSUE: Vertical Path (Y)")
        else:                   report.append("    -> MAIN ISSUE: Depth Control (Z)")
    else:
        report.append("  > STATUS: GOOD SHAPE MATCH")

    # ── 3. SPARC (optional) ───────────────────────────────────────────────────
    if sparc_metrics:
        ref = sparc_metrics["Reference"]
        pat = sparc_metrics["Patient"]
        sparc_grade_total = get_sparc_grade(pat["Total_SPARC"],    ref["Total_SPARC"])
        sparc_grade_low   = get_sparc_grade(pat["Low_Band_SPARC"], ref["Low_Band_SPARC"])
        sparc_grade_high  = get_sparc_grade(pat["High_Band_SPARC"],ref["High_Band_SPARC"])

        report.append("\nMOVEMENT SMOOTHNESS (SPARC)")
        report.append(f"  > Overall Grade:    {sparc_grade_total} / 10")
        report.append(f"  > Choppiness Grade: {sparc_grade_low} / 10  (0-5 Hz)")
        report.append(f"  > Tremor Grade:     {sparc_grade_high} / 10  (5-20 Hz)")
        report.append(f"  > Patient SPARC:    {pat['Total_SPARC']:.3f}")
        report.append(f"  > Ref SPARC:        {ref['Total_SPARC']:.3f}")
        report.append(f"  > Velocity RMSE:    {pat['Velocity_RMSE']:.4f} m/s")
        report.append(f"  > Peak Velocity:    {pat['Peak_Velocity']:.3f} m/s  "
                      f"(Ref: {ref['Peak_Velocity']:.3f} m/s)")

        if sparc_grade_total >= 9:
            report.append("  > STATUS: VERY SMOOTH MOVEMENT")
        elif sparc_grade_total >= 7:
            report.append("  > STATUS: ACCEPTABLE SMOOTHNESS")
        elif sparc_grade_total >= 5:
            report.append("  > STATUS: MILD CHOPPINESS DETECTED")
        else:
            report.append("  > STATUS: SIGNIFICANT JERKINESS / TREMOR")
            if sparc_grade_high < sparc_grade_low:
                report.append("    -> PRIMARY ISSUE: HIGH-FREQUENCY TREMOR")
            else:
                report.append("    -> PRIMARY ISSUE: CHOPPY MOVEMENT PATTERN")

    return "\n".join(report)


# =============================================================================
# STANDALONE EXECUTION (unchanged from original)
# =============================================================================

def run_analysis(ref_path, pat_path, sensitivity=3.0, noise_level=0.0, shape_limit_m=0.10):
    print(f"\n--- Loading Data ---")
    ref_data = extract_hand_data(ref_path)
    pat_data = extract_hand_data(pat_path)

    if noise_level > 0:
        pat_data = add_awgn(pat_data, noise_level)

    score, global_rmse, axis_rmse, temp_cen, query_cen = \
        calculate_mdtw_with_sensitivity(ref_data, pat_data, sensitivity)

    rom_ratio, rom_ratios = calculate_rom_metrics(ref_data, pat_data)
    rom_axis_grades = [get_rom_grade(r) for r in rom_ratios]
    avg_rom_grade   = int(round(np.mean(rom_axis_grades)))
    shape_grade     = get_shape_grade(global_rmse, shape_limit_m)

    # SPARC
    analyzer     = MovementAnalyzer()
    sparc_metrics = analyzer.compare_performances(ref_data, pat_data)

    report = generate_therapist_report(
        rom_ratio, avg_rom_grade, rom_axis_grades, rom_ratios,
        global_rmse, shape_grade, axis_rmse, shape_limit_m,
        sparc_metrics=sparc_metrics,
    )

    print(f"Reference: {os.path.basename(ref_path)}")
    print(f"Patient:   {os.path.basename(pat_path)}")
    print("=" * 50)
    print(f"PATIENT VIEW -> SCORE: {score}/100")
    print("=" * 50)
    print("THERAPIST VIEW -> ANALYTICS:")
    print(report)
    print("=" * 50)
    return score


if __name__ == "__main__":
    REF = r"C:\path\to\template.xlsx"
    PAT = r"C:\path\to\patient.xlsx"
    SENSITIVITY    = 3.0
    Shape_Tolerance = 0.20
    run_analysis(REF, PAT, sensitivity=SENSITIVITY, noise_level=0.0, shape_limit_m=Shape_Tolerance)
