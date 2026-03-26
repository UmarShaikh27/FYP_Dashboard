"""
normalize.py  –  Shoulder-relative normalization for the unified physiotherapy pipeline.

Extracted from shoulder_origin_offline.ipynb.

Adds to the input DataFrame:
    - upper_arm_length, forearm_length, total_arm_length  (mean-averaged anatomical constants)
    - wrist_relative_x/y/z    (wrist with shoulder as origin)
    - wrist_normalized_x/y/z  (body-agnostic, unit-less wrist path)

Usage:
    from normalize import normalize
    output_path = normalize("raw_capture.xlsx", "outputs/patient/1")
"""

import os
import numpy as np
import pandas as pd


# ─── Required column names ────────────────────────────────────────────────────
REQUIRED_COLS = [
    'Shoulder_x', 'Shoulder_y', 'Shoulder_z',
    'Elbow_x',    'Elbow_y',    'Elbow_z',
    'Wrist_x',    'Wrist_y',    'Wrist_z',
]


def _add_normalized_columns(df: pd.DataFrame):
    """
    Core normalisation logic (ported from the notebook).

    Returns (df_augmented, upper_arm_length, forearm_length, total_arm_length).
    """
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # ── Per-frame segment lengths ──────────────────────────────────────
    per_frame_upper = np.sqrt(
        (df['Elbow_x'] - df['Shoulder_x'])**2 +
        (df['Elbow_y'] - df['Shoulder_y'])**2 +
        (df['Elbow_z'] - df['Shoulder_z'])**2
    )
    per_frame_forearm = np.sqrt(
        (df['Wrist_x'] - df['Elbow_x'])**2 +
        (df['Wrist_y'] - df['Elbow_y'])**2 +
        (df['Wrist_z'] - df['Elbow_z'])**2
    )

    # ── Averaged scalars (anatomical constants) ────────────────────────
    upper_arm_length = float(per_frame_upper.mean())
    forearm_length   = float(per_frame_forearm.mean())
    total_arm_length = upper_arm_length + forearm_length

    df['upper_arm_length'] = upper_arm_length
    df['forearm_length']   = forearm_length
    df['total_arm_length'] = total_arm_length

    # ── Shoulder-relative wrist position ───────────────────────────────
    df['wrist_relative_x'] = df['Wrist_x'] - df['Shoulder_x']
    df['wrist_relative_y'] = df['Wrist_y'] - df['Shoulder_y']
    df['wrist_relative_z'] = df['Wrist_z'] - df['Shoulder_z']

    # ── Body-agnostic normalized wrist path ─────────────────────────────
    df['wrist_normalized_x'] = df['wrist_relative_x'] / total_arm_length
    df['wrist_normalized_y'] = df['wrist_relative_y'] / total_arm_length
    df['wrist_normalized_z'] = df['wrist_relative_z'] / total_arm_length

    return df, upper_arm_length, forearm_length, total_arm_length


def normalize(input_excel_path: str, output_dir: str) -> str:
    """
    Read a raw capture Excel, add normalized columns, and save.

    Parameters
    ----------
    input_excel_path : str
        Path to the raw Excel file with Shoulder/Elbow/Wrist x/y/z columns.
    output_dir : str
        Directory to write the output file.

    Returns
    -------
    str
        Path to the saved normalized Excel file.

    Raises
    ------
    ValueError
        If required columns are missing from the input file.
    FileNotFoundError
        If the input file does not exist.
    """
    if not os.path.isfile(input_excel_path):
        raise FileNotFoundError(f"Input file not found: {input_excel_path}")

    df = pd.read_excel(input_excel_path)
    df, upper, forearm, total = _add_normalized_columns(df)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "normalized.xlsx")
    df.to_excel(output_path, index=False)

    print(f"✅ Normalization complete")
    print(f"   upper_arm_length : {upper:.4f} m")
    print(f"   forearm_length   : {forearm:.4f} m")
    print(f"   total_arm_length : {total:.4f} m")
    print(f"   → Saved: {output_path}")

    return output_path
