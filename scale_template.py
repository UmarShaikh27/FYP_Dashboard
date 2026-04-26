"""
scale_template.py  –  Template scaling for the unified physiotherapy pipeline.

Extracted from scaling_template.ipynb.

Scales a normalized reference template to the patient's global coordinate
system using the patient's arm length and mean shoulder position.

Formula:
    wrist_scaled = (wrist_normalized * total_arm_length) + shoulder_mean

Usage:
    from scale_template import scale
    output_path = scale("template_normalized.xlsx",
                        "normalized.xlsx",
                        "outputs/patient/1")
"""

import os
import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers (ported from the notebook)
# ─────────────────────────────────────────────────────────────────────────────
def _extract_patient_scalars(patient_df: pd.DataFrame) -> dict:
    """
    Extract constant scalars from the patient normalised file.

    Returns a dict with:
        upper_arm_length, forearm_length, total_arm_length,
        shoulder_x, shoulder_y, shoulder_z
    """
    if 'total_arm_length' in patient_df.columns:
        total_arm_length = patient_df['total_arm_length'].iloc[0]
        upper_arm_length = patient_df['upper_arm_length'].iloc[0]
        forearm_length   = patient_df['forearm_length'].iloc[0]
    else:
        # Recompute from joint positions
        per_frame_upper = np.sqrt(
            (patient_df['Elbow_x'] - patient_df['Shoulder_x'])**2 +
            (patient_df['Elbow_y'] - patient_df['Shoulder_y'])**2 +
            (patient_df['Elbow_z'] - patient_df['Shoulder_z'])**2
        )
        per_frame_forearm = np.sqrt(
            (patient_df['Wrist_x'] - patient_df['Elbow_x'])**2 +
            (patient_df['Wrist_y'] - patient_df['Elbow_y'])**2 +
            (patient_df['Wrist_z'] - patient_df['Elbow_z'])**2
        )
        upper_arm_length = per_frame_upper.mean()
        forearm_length   = per_frame_forearm.mean()
        total_arm_length = upper_arm_length + forearm_length

    return {
        'upper_arm_length': float(upper_arm_length),
        'forearm_length'  : float(forearm_length),
        'total_arm_length': float(total_arm_length),
        'shoulder_x'      : float(patient_df['Shoulder_x'].mean()),
        'shoulder_y'      : float(patient_df['Shoulder_y'].mean()),
        'shoulder_z'      : float(patient_df['Shoulder_z'].mean()),
    }


def _scale_template(template_df: pd.DataFrame, scalars: dict) -> pd.DataFrame:
    """Scale normalized template to global coordinates.

    Formula:
        wrist_scaled = (wrist_normalized * total_arm_length) + shoulder_mean

    wrist_normalized is unit-less (ratio of arm length, shoulder as origin).
    Multiplying by arm length re-applies body size.
    Adding shoulder mean shifts from shoulder-origin back to global camera space.
    """
    arm = scalars['total_arm_length']
    df  = template_df.copy()

    df['wrist_scaled_x'] = (df['wrist_normalized_x'] * arm) + scalars['shoulder_x']
    df['wrist_scaled_y'] = (df['wrist_normalized_y'] * arm) + scalars['shoulder_y']
    df['wrist_scaled_z'] = (df['wrist_normalized_z'] * arm) + scalars['shoulder_z']

    return df


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────
def scale(template_path: str,
          patient_normalized_path: str,
          output_dir: str) -> str:
    """
    Scale a normalized template to the patient's coordinate system.

    Parameters
    ----------
    template_path : str
        Path to the normalized template Excel (must have wrist_normalized_x/y/z columns).
    patient_normalized_path : str
        Path to the patient's normalized Excel (to extract arm length & shoulder mean).
    output_dir : str
        Directory to write the output file.

    Returns
    -------
    str
        Path to the saved scaled template Excel file.
    """
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Template file not found: {template_path}")
    if not os.path.isfile(patient_normalized_path):
        raise FileNotFoundError(f"Patient file not found: {patient_normalized_path}")

    print("Loading files...")
    template_df = pd.read_excel(template_path)
    patient_df  = pd.read_excel(patient_normalized_path)

    # ── Validate template ──────────────────────────────────────────────
    required_cols = ['wrist_normalized_x', 'wrist_normalized_y', 'wrist_normalized_z']
    if not all(col in template_df.columns for col in required_cols):
        # Template files may be saved without headers. Try reloading with header=None
        template_df = pd.read_excel(template_path, header=None)
        if len(template_df.columns) >= 3:
            template_df = template_df.iloc[:, :3]
            template_df.columns = required_cols
            print("[INFO] Template missing headers. Automatically assigned wrist_normalized_x/y/z.")
        else:
            raise ValueError("Template file missing wrist_normalized_x/y/z columns and does not contain 3 unnamed columns.")

    # ── Validate patient ───────────────────────────────────────────────
    for col in ['Shoulder_x', 'Shoulder_y', 'Shoulder_z']:
        if col not in patient_df.columns:
            raise ValueError(f"Patient file missing column: {col}")

    # ── Extract scalars ────────────────────────────────────────────────
    scalars = _extract_patient_scalars(patient_df)

    print("\n=== Scalars Used (all constant) ===")
    print(f"  upper_arm_length : {scalars['upper_arm_length']:.4f} m")
    print(f"  forearm_length   : {scalars['forearm_length']:.4f} m")
    print(f"  total_arm_length : {scalars['total_arm_length']:.4f} m")
    print(f"  shoulder_x (mean): {scalars['shoulder_x']:.4f} m")
    print(f"  shoulder_y (mean): {scalars['shoulder_y']:.4f} m")
    print(f"  shoulder_z (mean): {scalars['shoulder_z']:.4f} m")

    # ── Scale ──────────────────────────────────────────────────────────
    scaled_df = _scale_template(template_df, scalars)

    # ── Save ───────────────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "template_scaled.xlsx")
    scaled_df.to_excel(output_path, index=False)

    print(f"\n✅ Scaled template saved: {output_path}")
    print(f"   Columns added: wrist_scaled_x, wrist_scaled_y, wrist_scaled_z")
    print(f"   Units: meters (global coordinates)")

    return output_path