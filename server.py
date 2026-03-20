"""
server.py - PhysioSync Local Backend
=====================================
Pipeline order:
  1. shoulder_origin.py  - records Shoulder/Elbow/Wrist, saves normalized Excel
  2. scale_template()    - scales normalized template to this patient's body (runs inside server)
  3. disected_mmDTW.py   - DTW comparison of scaled template vs patient Wrist_x/y/z

Install:
  pip install flask flask-cors numpy pandas tslearn openpyxl matplotlib mediapipe opencv-python pyrealsense2

Run:
  python server.py
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import subprocess
import threading
import os
import io
import base64
import time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import sys
sys.path.insert(0, os.path.dirname(__file__))

from disected_mmDTW import (
    calculate_mdtw_with_sensitivity,
    calculate_rom_metrics,
    get_rom_grade,
    get_shape_grade,
    get_sparc_grade,
    generate_therapist_report,
    MovementAnalyzer,
)

app = Flask(__name__)
CORS(app)

# ============================================================
# CONFIGURATION  —  edit these two lines
# ============================================================
MOCAP_MODEL_PATH = r"C:\Users\Umar Shaikh\OneDrive - Habib University\SEM 7\Dashboard App\models\pose_landmarker_lite.task"  # <-- EDIT THIS
MOCAP_ARM        = "right"   # "right" or "left"
# ============================================================

MOCAP_SCRIPT     = os.path.join(os.path.dirname(__file__), "shoulder_origin.py")
OUTPUT_FOLDER    = os.path.join(os.path.dirname(__file__), "output_excel")
TEMPLATES_FOLDER = os.path.join(os.path.dirname(__file__), "templates")

_mocap_process = None
_mocap_status  = {"state": "idle", "message": "", "output_file": None}

# ── Utilities ─────────────────────────────────────────────────────────────────

def latest_file_in(folder, extension=".xlsx"):
    """Returns the most recently modified file with given extension."""
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(extension)]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def figure_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120, facecolor="#0a0d12")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return b64


def excel_file_to_base64(excel_file_path):
    """
    Read an Excel file and convert it to base64 string.
    Returns base64 string if successful, None otherwise.
    """
    try:
        with open(excel_file_path, 'rb') as f:
            excel_data = f.read()
        b64 = base64.b64encode(excel_data).decode("utf-8")
        print(f"[Excel] Converted to base64: {os.path.basename(excel_file_path)} ({len(excel_data)} bytes)")
        return b64
    except Exception as e:
        print(f"[Excel] Failed to encode: {e}")
        return None


def build_comparison_figure(ref_centered, pat_centered, score, report_text, sparc_metrics=None):
    """
    Dark-themed plot:
      Top: 3D trajectory (mean-centred)
      Bottom: SPARC plots (velocity + spectrum) — only if sparc_metrics provided
    
    Note: Therapist report is displayed separately in the UI, not on the plot.
    """
    has_sparc = sparc_metrics is not None
    nrows = 2 if has_sparc else 1

    fig = plt.figure(figsize=(14, 10 if has_sparc else 6), facecolor="#0a0d12")

    # ── Row 1: 3D trajectory ──────────────────────────────────────────────────
    if has_sparc:
        ax1 = fig.add_subplot(2, 2, 1, projection="3d")
    else:
        ax1 = fig.add_subplot(1, 1, 1, projection="3d")
    ax1.set_facecolor("#111520")
    ax1.plot(ref_centered[:, 0], ref_centered[:, 1], ref_centered[:, 2],
             color="#0059ff", linestyle="--", linewidth=1.5, label="Expert (centred)")
    ax1.plot(pat_centered[:, 0], pat_centered[:, 1], pat_centered[:, 2],
             color="#00e5c3", linewidth=2.5, label="Patient (centred)")
    ax1.set_title(f"DTW Score: {score}/100", color="#00e5c3", fontsize=14, fontweight="bold", pad=10)
    ax1.legend(facecolor="#1a2030", labelcolor="#e8edf5", edgecolor="#232a3a", fontsize=8)
    ax1.set_xlabel("X", color="#6b7a96"); ax1.set_ylabel("Y", color="#6b7a96"); ax1.set_zlabel("Z", color="#6b7a96")
    ax1.tick_params(colors="#6b7a96")
    for spine in ax1.spines.values():
        spine.set_edgecolor("#232a3a")

    # ── Row 2: SPARC plots (velocity + spectrum) ──────────────────────────────
    if has_sparc:
        from scipy.signal import resample as sp_resample
        plot_data = sparc_metrics["Plot_Data"]
        ref_speed = plot_data["Ref_Speed"]
        pat_speed = plot_data["Pat_Speed"]

        analyzer = MovementAnalyzer()
        ref_freq, ref_spec = analyzer.get_spectrum_for_plot(ref_speed)
        pat_freq, pat_spec = analyzer.get_spectrum_for_plot(pat_speed)

        # Velocity profile
        ax3 = fig.add_subplot(2, 2, 3)
        ax3.set_facecolor("#111520")
        pat_speed_rs = sp_resample(pat_speed, len(ref_speed))
        ax3.plot(ref_speed,    color="#6b7a96", linestyle="--", linewidth=1.2, label="Expert Speed")
        ax3.plot(pat_speed_rs, color="#00e5c3", linewidth=1.8,               label="Patient Speed")
        ax3.set_title("Velocity Profile (Time Domain)", color="#e8edf5", fontsize=11, fontweight="bold")
        ax3.set_xlabel("Samples", color="#6b7a96"); ax3.set_ylabel("Speed (m/s)", color="#6b7a96")
        ax3.tick_params(colors="#6b7a96"); ax3.set_facecolor("#111520")
        ax3.spines["bottom"].set_color("#232a3a"); ax3.spines["left"].set_color("#232a3a")
        ax3.spines["top"].set_color("#232a3a");    ax3.spines["right"].set_color("#232a3a")
        vel_rmse = sparc_metrics["Patient"]["Velocity_RMSE"]
        ax3.text(0.02, 0.92, f"Vel RMSE: {vel_rmse:.4f} m/s", transform=ax3.transAxes,
                 color="#e8edf5", fontsize=8, bbox=dict(facecolor="#1a2030", alpha=0.7, edgecolor="#232a3a"))
        ax3.legend(facecolor="#1a2030", labelcolor="#e8edf5", edgecolor="#232a3a", fontsize=8)
        ax3.grid(True, alpha=0.15, color="#232a3a")

        # Spectral complexity
        ax4 = fig.add_subplot(2, 2, 4)
        ax4.set_facecolor("#111520")
        mask_r = ref_freq <= 25; mask_p = pat_freq <= 25
        ax4.plot(ref_freq[mask_r], ref_spec[mask_r], color="#6b7a96", linestyle="--", linewidth=1.2, label="Expert Spectrum")
        ax4.plot(pat_freq[mask_p], pat_spec[mask_p], color="#0090ff", linewidth=1.8,                label="Patient Spectrum")
        ax4.axvspan(0,  5, color="#00e5c3", alpha=0.07, label="Shape / Choppiness (0-5Hz)")
        ax4.axvspan(5, 20, color="#ff4b6e", alpha=0.07, label="Tremor (5-20Hz)")
        ax4.set_title("Spectral Complexity (SPARC)", color="#e8edf5", fontsize=11, fontweight="bold")
        ax4.set_xlabel("Frequency (Hz)", color="#6b7a96"); ax4.set_ylabel("Norm. Magnitude", color="#6b7a96")
        ax4.tick_params(colors="#6b7a96")
        ax4.spines["bottom"].set_color("#232a3a"); ax4.spines["left"].set_color("#232a3a")
        ax4.spines["top"].set_color("#232a3a");    ax4.spines["right"].set_color("#232a3a")
        pat_s = sparc_metrics["Patient"]
        score_text = (f"Choppiness: {pat_s['Low_Band_SPARC']:.2f}\nTremor: {pat_s['High_Band_SPARC']:.2f}")
        ax4.text(0.55, 0.7, score_text, transform=ax4.transAxes,
                 color="#e8edf5", fontsize=9, bbox=dict(facecolor="#1a2030", alpha=0.85, edgecolor="#0090ff"))
        ax4.legend(facecolor="#1a2030", labelcolor="#e8edf5", edgecolor="#232a3a", fontsize=8)
        ax4.grid(True, alpha=0.15, color="#232a3a")

    fig.tight_layout(pad=2.5)
    return figure_to_base64(fig)


# ── Template scaling (from scaling_template.ipynb) ────────────────────────────

def extract_patient_scalars(patient_df):
    """
    Reads arm length scalars and mean shoulder position from patient Excel.
    shoulder_origin.py saves these as constant columns automatically.
    Falls back to computing them from joint positions if columns are absent.
    """
    if 'total_arm_length' in patient_df.columns:
        # shoulder_origin.py already computed and saved these
        total_arm_length = patient_df['total_arm_length'].iloc[0]
        upper_arm_length = patient_df['upper_arm_length'].iloc[0]
        forearm_length   = patient_df['forearm_length'].iloc[0]
    else:
        # Fallback: compute from raw joint positions
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
        'upper_arm_length': upper_arm_length,
        'forearm_length':   forearm_length,
        'total_arm_length': total_arm_length,
        'shoulder_x':       patient_df['Shoulder_x'].mean(),
        'shoulder_y':       patient_df['Shoulder_y'].mean(),
        'shoulder_z':       patient_df['Shoulder_z'].mean(),
    }


def scale_template(template_df, scalars):
    """
    Converts a normalized template to the patient's global coordinates.

    Formula (from scaling_template.ipynb):
        wrist_scaled = (wrist_normalized * total_arm_length) + shoulder_mean

    Accepts either column naming convention:
      - wrist_normalized_x/y/z  (DBA pipeline output)
      - Wrist_x/y/z             (already-normalized template with standard column names)

    The result is in the same metric space as the patient's raw Wrist_x/y/z,
    so DTW comparison is body-size agnostic.
    """
    arm = scalars['total_arm_length']
    df  = template_df.copy()

    # Detect which column naming convention the template uses
    if 'wrist_normalized_x' in df.columns:
        src_x, src_y, src_z = 'wrist_normalized_x', 'wrist_normalized_y', 'wrist_normalized_z'
    else:
        # Wrist_x/y/z are normalized (confirmed by user) — same formula applies
        src_x, src_y, src_z = 'Wrist_x', 'Wrist_y', 'Wrist_z'

    df['wrist_scaled_x'] = (df[src_x] * arm) + scalars['shoulder_x']
    df['wrist_scaled_y'] = (df[src_y] * arm) + scalars['shoulder_y']
    df['wrist_scaled_z'] = (df[src_z] * arm) + scalars['shoulder_z']
    return df

def load_patient_wrist(patient_df):
    """
    Extracts the patient's raw wrist trajectory as a numpy array.
    shoulder_origin.py saves columns as: Wrist_x, Wrist_y, Wrist_z
    """
    cols = ['Wrist_x', 'Wrist_y', 'Wrist_z']
    missing = [c for c in cols if c not in patient_df.columns]
    if missing:
        raise KeyError(f"Patient file missing columns: {missing}. "
                       f"Make sure it was recorded with shoulder_origin.py")
    return patient_df[cols].dropna().values


def load_scaled_template_wrist(template_df, scalars=None):
    """
    Loads and scales the reference wrist trajectory from a template file.

    Priority order:
      1. wrist_normalized_x/y/z  → scale using patient scalars
      2. Wrist_x/y/z             → treat as normalized, scale using patient scalars
      3. wrist_scaled_x/y/z      → already scaled externally, use directly
      4. x/y/z                   → generic fallback, use directly

    Cases 1 and 2 require scalars to be provided (not None).
    Cases 3 and 4 use the values directly.
    """
    norm_cols    = ['wrist_normalized_x', 'wrist_normalized_y', 'wrist_normalized_z']
    raw_cols     = ['Wrist_x',            'Wrist_y',            'Wrist_z']
    scaled_cols  = ['wrist_scaled_x',     'wrist_scaled_y',     'wrist_scaled_z']
    generic_cols = ['x',                  'y',                  'z']

    if all(c in template_df.columns for c in norm_cols):
        # wrist_normalized_x/y/z — scale to patient body coordinates
        if scalars is None:
            raise ValueError("scalars must be provided to scale a normalized template.")
        scaled_df = scale_template(template_df, scalars)
        return scaled_df[scaled_cols].dropna().values

    elif all(c in template_df.columns for c in raw_cols):
        # Wrist_x/y/z confirmed as normalized by user — scale to patient body coordinates
        if scalars is None:
            raise ValueError("scalars must be provided to scale a Wrist_x/y/z normalized template.")
        scaled_df = scale_template(template_df, scalars)
        return scaled_df[scaled_cols].dropna().values

    elif all(c in template_df.columns for c in scaled_cols):
        # Already scaled externally (e.g. by scaling_template.ipynb)
        return template_df[scaled_cols].dropna().values

    elif all(c in template_df.columns for c in generic_cols):
        # Generic fallback
        return template_df[generic_cols].dropna().values

    else:
        found = list(template_df.columns)
        raise KeyError(
            f"Template file columns not recognised. Found: {found}\n"
            "Expected one of:\n"
            "  - wrist_normalized_x/y/z  (normalized DBA template)\n"
            "  - Wrist_x/y/z             (normalized template with standard column names)\n"
            "  - wrist_scaled_x/y/z      (pre-scaled template)\n"
            "  - x/y/z                   (generic)"
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "2.0"})


@app.route("/templates", methods=["GET"])
def list_templates():
    os.makedirs(TEMPLATES_FOLDER, exist_ok=True)
    files = [f for f in os.listdir(TEMPLATES_FOLDER) if f.endswith(".xlsx")]
    return jsonify({"templates": files})


@app.route("/mocap/start", methods=["POST"])
def mocap_start():
    """
    Launches shoulder_origin.py as a subprocess.
    Config is passed via environment variables — no interactive prompts.

    Body: {
      "duration":      8,
      "grace":         6,
      "exercise":      "Wrist Rotation",
      "trail":         "trail_1",
      "arm":           "right"
    }
    """
    global _mocap_process, _mocap_status

    if _mocap_process and _mocap_process.poll() is None:
        return jsonify({"error": "Recording already in progress"}), 400

    data     = request.get_json(force=True)
    duration = float(data.get("duration", 8))
    grace    = float(data.get("grace",    6))
    exercise = data.get("exercise", "exercise")
    trail    = data.get("trail",    "trail_1")
    arm      = data.get("arm",      MOCAP_ARM)

    _mocap_status = {
        "state":   "grace",
        "message": f"Camera opening — press SPACE in the camera window to start countdown…",
        "output_file": None,
    }

    def run():
        global _mocap_process, _mocap_status
        try:
            # Pass all config as environment variables to shoulder_origin.py
            env = os.environ.copy()
            env["MOCAP_CAMERA"]      = "realsense"
            env["MOCAP_ARM"]         = arm
            env["MOCAP_DURATION"]    = str(duration)
            env["MOCAP_GRACE"]       = str(grace)
            env["MOCAP_EXERCISE"]    = exercise
            env["MOCAP_TRAIL"]       = trail
            env["MOCAP_OUTPUT_DIR"]  = OUTPUT_FOLDER
            env["MOCAP_MODEL_PATH"]  = MOCAP_MODEL_PATH

            _mocap_process = subprocess.Popen(
                [sys.executable, MOCAP_SCRIPT],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = _mocap_process.communicate()

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            # Print full output to server terminal so you can always see it there
            if stdout_str.strip():
                print("[MOCAP STDOUT]\n" + stdout_str)
            if stderr_str.strip():
                print("[MOCAP STDERR]\n" + stderr_str)

            # ── Determine success ─────────────────────────────────────────────
            # shoulder_origin.py prints "Excel Saved: <path>" on success.
            # We treat the run as successful if:
            #   (a) returncode == 0, OR
            #   (b) a new Excel file exists in output_excel/ AND stdout mentions "Excel Saved"
            # This handles MediaPipe/OpenCV writing to stderr on clean exits,
            # which causes a non-zero returncode on some platforms.
            excel_saved_in_stdout = "Excel Saved" in stdout_str
            output_file = latest_file_in(OUTPUT_FOLDER)
            succeeded = (
                _mocap_process.returncode == 0
                or (excel_saved_in_stdout and output_file is not None)
            )

            if succeeded:
                _mocap_status = {
                    "state":       "done",
                    "message":     "Recording complete. Ready to analyze.",
                    "output_file": os.path.basename(output_file) if output_file else None,
                    "stdout":      stdout_str,
                    "stderr":      stderr_str,
                }
            else:
                # Extract just the Traceback section — skip MediaPipe warning noise
                lines = stderr_str.splitlines()
                tb_lines = []
                in_traceback = False
                for line in lines:
                    if line.startswith("Traceback"):
                        in_traceback = True
                    if in_traceback:
                        tb_lines.append(line)

                # Fallback: show last 30 lines if no traceback found
                if tb_lines:
                    clean_error = "\n".join(tb_lines)
                else:
                    clean_error = "\n".join(lines[-30:]) if lines else "No error output captured."

                _mocap_status = {
                    "state":       "error",
                    "message":     clean_error,
                    "full_stderr": stderr_str,
                    "output_file": None,
                }
        except Exception as e:
            import traceback as tb
            _mocap_status = {"state": "error", "message": str(e), "full_stderr": tb.format_exc(), "output_file": None}

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/mocap/status", methods=["GET"])
def mocap_status():
    return jsonify(_mocap_status)


@app.route("/mocap/logs", methods=["GET"])
def mocap_logs():
    """Returns full stderr output from the last mocap run — useful for debugging."""
    return jsonify({
        "full_stderr": _mocap_status.get("full_stderr", ""),
        "stdout":      _mocap_status.get("stdout", ""),
        "state":       _mocap_status.get("state", "idle"),
    })


@app.route("/mocap/stop", methods=["POST"])
def mocap_stop():
    global _mocap_process
    if _mocap_process and _mocap_process.poll() is None:
        _mocap_process.terminate()
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not_running"})


@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Full pipeline: load patient file → extract scalars → scale template → DTW.

    Body: {
      "patient_file":    "right_arm_exercise_realsense_trail_1.xlsx",
      "template_file":   "stage3_normalized_soft_dba_gi_centroid.xlsx",
      "sensitivity":     3.0,
      "shape_tolerance": 0.20
    }
    """
    data = request.get_json(force=True)

    patient_file    = data.get("patient_file")
    template_file   = data.get("template_file")
    sensitivity     = float(data.get("sensitivity",     3.0))
    shape_tolerance = float(data.get("shape_tolerance", 0.20))

    if not patient_file or not template_file:
        return jsonify({"error": "patient_file and template_file are required"}), 400

    pat_path = os.path.join(OUTPUT_FOLDER,    patient_file)
    ref_path = os.path.join(TEMPLATES_FOLDER, template_file)

    if not os.path.exists(pat_path):
        return jsonify({"error": f"Patient file not found: {patient_file}"}), 404
    if not os.path.exists(ref_path):
        return jsonify({"error": f"Template file not found: {template_file}"}), 404

    try:
        patient_df  = pd.read_excel(pat_path)
        template_df = pd.read_excel(ref_path)

        # ── Detect template type ──────────────────────────────────────────────
        # Scaling applies when the template contains normalized wrist coordinates.
        # This includes both:
        #   - wrist_normalized_x/y/z  (DBA pipeline naming)
        #   - Wrist_x/y/z             (confirmed normalized by user — same formula applies)
        # In both cases we need patient scalars (arm length + shoulder position)
        # to convert the normalized path into the patient's metric space.
        norm_cols_dba = ['wrist_normalized_x', 'wrist_normalized_y', 'wrist_normalized_z']
        norm_cols_std = ['Wrist_x', 'Wrist_y', 'Wrist_z']
        scaled_cols   = ['wrist_scaled_x', 'wrist_scaled_y', 'wrist_scaled_z']

        template_is_normalized = (
            all(c in template_df.columns for c in norm_cols_dba) or
            all(c in template_df.columns for c in norm_cols_std)
        )
        # Pre-scaled templates (wrist_scaled_x/y/z) don't need patient scalars
        template_is_prescaled = all(c in template_df.columns for c in scaled_cols)

        # ── Step 1: Extract patient body scalars (needed for scaling) ─────────
        scalars = None
        if template_is_normalized and not template_is_prescaled:
            scalars = extract_patient_scalars(patient_df)
            scaling_note = (
                f"\nPATIENT BODY SCALARS (scaling applied)\n"
                f"  Upper arm : {scalars['upper_arm_length']:.4f} m\n"
                f"  Forearm   : {scalars['forearm_length']:.4f} m\n"
                f"  Total arm : {scalars['total_arm_length']:.4f} m\n"
                f"  Shoulder  : ({scalars['shoulder_x']:.3f}, "
                f"{scalars['shoulder_y']:.3f}, {scalars['shoulder_z']:.3f}) m"
            )
        elif template_is_prescaled:
            scaling_note = "\nSCALING: Template was pre-scaled externally, used directly."
        else:
            scaling_note = "\nSCALING: No recognized normalized columns found, used raw values."

        # ── Step 2: Load reference trajectory ────────────────────────────────
        ref_data = load_scaled_template_wrist(template_df, scalars)

        # ── Step 3: Extract patient wrist trajectory ──────────────────────────
        pat_data = load_patient_wrist(patient_df)

        # ── DIAGNOSTICS — printed to server terminal ──────────────────────────
        print("=" * 55)
        print("ANALYSIS DIAGNOSTICS")
        print("=" * 55)
        print(f"Template file   : {template_file}")
        print(f"Patient file    : {patient_file}")
        print(f"Template cols   : {list(template_df.columns)}")
        print(f"Template normalized: {template_is_normalized}")
        print(f"Template prescaled : {template_is_prescaled}")
        if scalars:
            print(f"Arm length      : {scalars['total_arm_length']:.4f} m")
            print(f"Shoulder pos    : ({scalars['shoulder_x']:.4f}, {scalars['shoulder_y']:.4f}, {scalars['shoulder_z']:.4f})")
        else:
            print("Scalars         : None (no scaling applied)")
        print(f"Template range X: {ref_data[:,0].min():.4f} to {ref_data[:,0].max():.4f}  (ptp={np.ptp(ref_data[:,0]):.4f})")
        print(f"Template range Y: {ref_data[:,1].min():.4f} to {ref_data[:,1].max():.4f}  (ptp={np.ptp(ref_data[:,1]):.4f})")
        print(f"Template range Z: {ref_data[:,2].min():.4f} to {ref_data[:,2].max():.4f}  (ptp={np.ptp(ref_data[:,2]):.4f})")
        print(f"Patient  range X: {pat_data[:,0].min():.4f} to {pat_data[:,0].max():.4f}  (ptp={np.ptp(pat_data[:,0]):.4f})")
        print(f"Patient  range Y: {pat_data[:,1].min():.4f} to {pat_data[:,1].max():.4f}  (ptp={np.ptp(pat_data[:,1]):.4f})")
        print(f"Patient  range Z: {pat_data[:,2].min():.4f} to {pat_data[:,2].max():.4f}  (ptp={np.ptp(pat_data[:,2]):.4f})")
        print("=" * 55)

        # ── Step 4: DTW analysis ─────────────────────────────────────────────
        # calculate_mdtw_with_sensitivity now returns 5 values (added centered arrays)
        score, global_rmse, axis_rmse, ref_centered, pat_centered =             calculate_mdtw_with_sensitivity(ref_data, pat_data, sensitivity)

        rom_ratio, rom_ratios = calculate_rom_metrics(ref_data, pat_data)
        rom_axis_grades = [get_rom_grade(r) for r in rom_ratios]
        avg_rom_grade   = int(round(float(np.mean(rom_axis_grades))))
        shape_grade     = get_shape_grade(global_rmse, shape_tolerance)

        # ── Step 5: SPARC smoothness analysis ────────────────────────────────
        analyzer      = MovementAnalyzer()
        sparc_metrics = analyzer.compare_performances(ref_data, pat_data, use_filter=True)

        ref_s = sparc_metrics["Reference"]
        pat_s = sparc_metrics["Patient"]
        sparc_grade_total = get_sparc_grade(pat_s["Total_SPARC"],    ref_s["Total_SPARC"])
        sparc_grade_low   = get_sparc_grade(pat_s["Low_Band_SPARC"], ref_s["Low_Band_SPARC"])
        sparc_grade_high  = get_sparc_grade(pat_s["High_Band_SPARC"],ref_s["High_Band_SPARC"])

        # ── Step 6: Report ────────────────────────────────────────────────────
        report_text = generate_therapist_report(
            rom_ratio, avg_rom_grade, rom_axis_grades, rom_ratios,
            global_rmse, shape_grade, axis_rmse, shape_tolerance,
            sparc_metrics=sparc_metrics,
        )
        full_report = report_text + scaling_note

        # ── Step 7: Build 4-panel plot (3D + report + velocity + spectrum) ───
        plot_b64 = build_comparison_figure(
            ref_centered, pat_centered, score, full_report,
            sparc_metrics=sparc_metrics,
        )

        # ── Step 8: Encode Excel file to base64 for storage in Firestore ────
        excel_b64 = excel_file_to_base64(pat_path)

        return jsonify({
            "score":           score,
            "global_rmse":     round(float(global_rmse), 4),
            "axis_rmse":       {
                "x": round(float(axis_rmse[0]), 4),
                "y": round(float(axis_rmse[1]), 4),
                "z": round(float(axis_rmse[2]), 4),
            },
            "rom_ratio":       round(float(rom_ratio), 4),
            "rom_ratios":      {
                "x": round(float(rom_ratios[0]), 4),
                "y": round(float(rom_ratios[1]), 4),
                "z": round(float(rom_ratios[2]), 4),
            },
            "rom_axis_grades":  rom_axis_grades,
            "avg_rom_grade":    avg_rom_grade,
            "shape_grade":      shape_grade,
            # ── SPARC ──
            "sparc": {
                "total":        round(float(pat_s["Total_SPARC"]),    4),
                "low_band":     round(float(pat_s["Low_Band_SPARC"]), 4),
                "high_band":    round(float(pat_s["High_Band_SPARC"]),4),
                "velocity_rmse":round(float(pat_s["Velocity_RMSE"]),  4),
                "peak_velocity":round(float(pat_s["Peak_Velocity"]),  4),
                "ref_total":    round(float(ref_s["Total_SPARC"]),    4),
            },
            "sparc_grades": {
                "total":     sparc_grade_total,
                "choppiness": sparc_grade_low,
                "tremor":    sparc_grade_high,
            },
            "report_text":      full_report,
            "plot_image_b64":   plot_b64,
            "patient_file":     patient_file,
            "template_file":    template_file,
            "excel_file_b64":   excel_b64,  # Base64-encoded Excel file for storage in Firestore
            "scalars": {
                "upper_arm_length": round(scalars["upper_arm_length"], 4) if scalars else None,
                "forearm_length":   round(scalars["forearm_length"],   4) if scalars else None,
                "total_arm_length": round(scalars["total_arm_length"], 4) if scalars else None,
                "scaling_applied":  template_is_normalized,
            },
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/files/patient", methods=["GET"])
def list_patient_files():
    if not os.path.exists(OUTPUT_FOLDER):
        return jsonify({"files": []})
    files = sorted(
        [f for f in os.listdir(OUTPUT_FOLDER) if f.endswith(".xlsx")],
        reverse=True
    )
    return jsonify({"files": files})


if __name__ == "__main__":
    print("=" * 60)
    print("  PhysioSync Backend  —  http://localhost:5050")
    print("=" * 60)
    print(f"  Mocap script  : {MOCAP_SCRIPT}")
    print(f"  Model path    : {MOCAP_MODEL_PATH}")
    print(f"  Templates     : {TEMPLATES_FOLDER}")
    print(f"  Output folder : {OUTPUT_FOLDER}")
    print()
    print("  TEMPLATES folder: put your normalized template .xlsx files here")
    print("  OUTPUT folder:    recorded files appear here after each session")
    print()
    if not os.path.exists(MOCAP_MODEL_PATH):
        print("  WARNING: MOCAP_MODEL_PATH not found. Edit server.py line 38.")
    print("=" * 60)
    os.makedirs(TEMPLATES_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    app.run(host="0.0.0.0", port=5050, debug=False)
