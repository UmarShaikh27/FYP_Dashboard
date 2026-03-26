"""
filter_data.py  –  3-stage motion filtering for the unified physiotherapy pipeline.

Extracted from Filtering/3stage_filtering.ipynb.

Stage 1 – Velocity-based segment removal
    Computes frame-to-frame velocity of the 3D wrist trajectory.
    Removes segments where velocity exceeds median + k * IQR.
    Interpolates removed segments using cubic spline.

Stage 2 – 3D residual spike removal
    After Stage 1, computes residual from a rolling-median baseline.
    Removes remaining spikes that exceed a 3D distance threshold.
    Interpolates the flagged points.

Stage 3 – Savitzky-Golay smoothing
    Applies scipy.signal.savgol_filter to smooth the trajectory.

Only the wrist_normalized_x/y/z columns are filtered; all other columns
(raw joints, arm lengths, etc.) are left untouched.

Usage:
    from filter_data import filter_motion
    output_path = filter_motion("normalized.xlsx", "outputs/patient/1")
"""

import os
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
from scipy.interpolate import CubicSpline


# ─── Column names that are filtered ──────────────────────────────────────────
TARGET_COLS = ['wrist_normalized_x', 'wrist_normalized_y', 'wrist_normalized_z']

# ─── Default hyper-parameters (matching the original notebook) ───────────────
VELOCITY_IQR_MULTIPLIER = 1.5      # Stage 1: k for outlier threshold
ROLLING_WINDOW          = 7        # Stage 2: rolling-median window size
SPIKE_DISTANCE_MULT     = 3.0      # Stage 2: distance threshold multiplier
SAVGOL_WINDOW           = 11       # Stage 3: Savitzky-Golay window length
SAVGOL_POLY_ORDER       = 3        # Stage 3: polynomial order


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 1 – Velocity-based segment removal
# ─────────────────────────────────────────────────────────────────────────────
def _remove_velocity_outliers(df: pd.DataFrame,
                              cols: list,
                              k: float = VELOCITY_IQR_MULTIPLIER) -> pd.DataFrame:
    """Remove segments where frame-to-frame velocity is an outlier."""
    df = df.copy()

    # Compute per-frame 3D velocity
    dx = df[cols[0]].diff()
    dy = df[cols[1]].diff()
    dz = df[cols[2]].diff()
    velocity = np.sqrt(dx**2 + dy**2 + dz**2)

    # IQR-based threshold
    q1 = velocity.quantile(0.25)
    q3 = velocity.quantile(0.75)
    iqr = q3 - q1
    threshold = q3 + k * iqr

    # Identify bad frames (skip first frame which has NaN velocity)
    bad_mask = velocity > threshold
    bad_mask.iloc[0] = False

    bad_indices = bad_mask[bad_mask].index.tolist()
    good_indices = bad_mask[~bad_mask].index.tolist()

    if len(bad_indices) == 0 or len(good_indices) < 4:
        return df

    # Interpolate bad frames using cubic spline anchored on clean frames
    for col in cols:
        clean_x = np.array(good_indices, dtype=float)
        clean_y = df.loc[good_indices, col].values
        cs = CubicSpline(clean_x, clean_y)
        df.loc[bad_indices, col] = cs(np.array(bad_indices, dtype=float))

    print(f"Stage 1: Velocity-based segment removal...")
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 2 – 3D residual spike removal
# ─────────────────────────────────────────────────────────────────────────────
def _remove_3d_outliers(df: pd.DataFrame,
                        cols: list,
                        window: int = ROLLING_WINDOW,
                        dist_mult: float = SPIKE_DISTANCE_MULT) -> pd.DataFrame:
    """Remove residual 3D spikes based on rolling-median baseline."""
    df = df.copy()

    # Compute rolling median as baseline
    baseline = pd.DataFrame({
        c: df[c].rolling(window=window, center=True, min_periods=1).median()
        for c in cols
    })

    # Compute 3D distance from baseline
    dist = np.sqrt(
        (df[cols[0]] - baseline[cols[0]])**2 +
        (df[cols[1]] - baseline[cols[1]])**2 +
        (df[cols[2]] - baseline[cols[2]])**2
    )

    median_dist = dist.median()
    threshold = median_dist * dist_mult if median_dist > 0 else 0.01

    bad_mask = dist > threshold
    bad_indices = bad_mask[bad_mask].index.tolist()
    good_indices = bad_mask[~bad_mask].index.tolist()

    print(f"Stage 2: 3D residual spike removal...")
    print(f"  3D filter: {len(bad_indices)} residual spikes removed")

    if len(bad_indices) == 0 or len(good_indices) < 4:
        return df

    for col in cols:
        clean_x = np.array(good_indices, dtype=float)
        clean_y = df.loc[good_indices, col].values
        cs = CubicSpline(clean_x, clean_y)
        df.loc[bad_indices, col] = cs(np.array(bad_indices, dtype=float))

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  Stage 3 – Savitzky-Golay smoothing
# ─────────────────────────────────────────────────────────────────────────────
def _smooth_signal(df: pd.DataFrame,
                   cols: list,
                   window_length: int = SAVGOL_WINDOW,
                   poly_order: int = SAVGOL_POLY_ORDER) -> pd.DataFrame:
    """Apply Savitzky-Golay filter to each target column."""
    df = df.copy()
    print(f"Stage 3: Savitzky-Golay smoothing...")

    n = len(df)
    # window must be odd and > poly_order
    wl = min(window_length, n)
    if wl % 2 == 0:
        wl -= 1
    if wl <= poly_order:
        return df  # too few points to smooth

    for col in cols:
        df[col] = savgol_filter(df[col].values, wl, poly_order)

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────
def filter_motion(input_excel_path: str, output_dir: str) -> str:
    """
    Apply the 3-stage filter to a normalized Excel file and save.

    Parameters
    ----------
    input_excel_path : str
        Path to the normalized Excel file (must contain wrist_normalized_x/y/z).
    output_dir : str
        Directory to write the output file.

    Returns
    -------
    str
        Path to the saved filtered Excel file.

    Raises
    ------
    ValueError
        If required columns are missing.
    FileNotFoundError
        If the input file does not exist.
    """
    if not os.path.isfile(input_excel_path):
        raise FileNotFoundError(f"Input file not found: {input_excel_path}")

    df = pd.read_excel(input_excel_path)

    missing = [c for c in TARGET_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns for filtering: {missing}")

    # ── Stage 1 ────────────────────────────────────────────────────────
    df = _remove_velocity_outliers(df, TARGET_COLS)

    # ── Stage 2 ────────────────────────────────────────────────────────
    df = _remove_3d_outliers(df, TARGET_COLS)

    # ── Stage 3 ────────────────────────────────────────────────────────
    df = _smooth_signal(df, TARGET_COLS)

    # ── Save ───────────────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "filtered.xlsx")
    df.to_excel(output_path, index=False)

    print(f"\n✅ Filtering complete → {output_path}")
    return output_path
