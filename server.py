"""
server.py - PhysioSync Local Backend
=====================================
Pipeline order:
  1. shoulder_origin.py  - records Shoulder/Elbow/Wrist, saves normalized Excel
  2. filter_data.py      - 3-stage filter on wrist_normalized_x/y/z
  3. scale_template.py   - scales normalized template to patient body coordinates
  4. score.py            - DTW + SPARC scoring on filtered patient vs scaled template

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

# Import from the new unified pipeline modules
from score import (
    calculate_mdtw_with_sensitivity,
    calculate_rom_metrics,
    get_rom_grade,
    get_shape_grade,
    generate_therapist_report,
    MovementAnalyzer,
)
from scale_template import scale as scale_template_file
from filter_data import filter_motion

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
        # shoulder_origin.py already produces all normalized columns
        # (wrist_normalized_x/y/z, Shoulder_x/y/z, total_arm_length, etc.)
        # so it is equivalent to capture.py + normalize.py in the friend's pipeline.
        # pat_path is therefore treated as the "normalized" file for scaling.

        # ── Temp working directory for intermediate files ──────────────────────
        temp_dir = os.path.join(OUTPUT_FOLDER, "_temp_pipeline")
        os.makedirs(temp_dir, exist_ok=True)

        # ── Step 1: Filter patient data (matches main_pipeline Stage 3) ───────
        # filter_motion runs on the shoulder_origin output (already normalized).
        # Only wrist_normalized_x/y/z are modified; all other columns preserved.
        filtered_path = filter_motion(pat_path, temp_dir)

        # ── Step 2: Scale template (matches main_pipeline Stage 4) ────────────
        # CRITICAL: scale uses the NORMALIZED file (pat_path = shoulder_origin
        # output), NOT the filtered file. This matches main_pipeline.py exactly:
        #   scaled_template_path = scale(template, patient_normalized_path=normalized_path)
        # The shoulder/arm-length scalars must come from the unfiltered file
        # because filtering only touches wrist_normalized_x/y/z — the Shoulder
        # and arm-length columns are identical in both files, but using pat_path
        # is correct by definition and matches the friend's pipeline.
        scaled_template_path = scale_template_file(
            template_path=ref_path,
            patient_normalized_path=pat_path,   # normalized = shoulder_origin output
            output_dir=temp_dir,
        )

        # ── Step 3: Load data for scoring (matches main_pipeline Stage 5) ─────
        # score_movement(patient_filtered_path=filtered_path,
        #                template_scaled_path=scaled_template_path)
        # _extract_patient_global_trajectory_from_filtered reconstructs global
        # wrist coords from: wrist_normalized * total_arm_length + Shoulder_mean
        from score import (
            _extract_patient_global_trajectory_from_filtered,
            _require_columns,
            TEMPLATE_COLS,
        )

        pat_df = pd.read_excel(filtered_path)
        ref_df = pd.read_excel(scaled_template_path)

        _require_columns(ref_df, TEMPLATE_COLS, "Scaled template")
        ref_df_clean = ref_df.dropna(subset=TEMPLATE_COLS)
        ref_data = ref_df_clean[TEMPLATE_COLS].to_numpy(dtype=float)
        pat_data, pat_source = _extract_patient_global_trajectory_from_filtered(pat_df)

        # ── DIAGNOSTICS ───────────────────────────────────────────────────────
        print("=" * 55)
        print("ANALYSIS DIAGNOSTICS")
        print("=" * 55)
        print(f"Patient file    : {patient_file}")
        print(f"Template file   : {template_file}")
        print(f"Filtered file   : {filtered_path}")
        print(f"Scaled template : {scaled_template_path}")
        print(f"Patient source  : {pat_source}")
        print(f"Patient rows    : {len(pat_data)}  Template rows: {len(ref_data)}")
        print(f"Patient  range X: {pat_data[:,0].min():.4f} to {pat_data[:,0].max():.4f}  (ptp={np.ptp(pat_data[:,0]):.4f})")
        print(f"Patient  range Y: {pat_data[:,1].min():.4f} to {pat_data[:,1].max():.4f}  (ptp={np.ptp(pat_data[:,1]):.4f})")
        print(f"Patient  range Z: {pat_data[:,2].min():.4f} to {pat_data[:,2].max():.4f}  (ptp={np.ptp(pat_data[:,2]):.4f})")
        print(f"Template range X: {ref_data[:,0].min():.4f} to {ref_data[:,0].max():.4f}  (ptp={np.ptp(ref_data[:,0]):.4f})")
        print(f"Template range Y: {ref_data[:,1].min():.4f} to {ref_data[:,1].max():.4f}  (ptp={np.ptp(ref_data[:,1]):.4f})")
        print(f"Template range Z: {ref_data[:,2].min():.4f} to {ref_data[:,2].max():.4f}  (ptp={np.ptp(ref_data[:,2]):.4f})")
        print("=" * 55)

        # ── Step 4: ROM metrics ───────────────────────────────────────────────
        rom_ratio, rom_ratios = calculate_rom_metrics(ref_data, pat_data)
        rom_axis_grades = [get_rom_grade(float(r)) for r in rom_ratios]
        avg_rom_grade   = int(round(float(np.mean(rom_axis_grades))))

        # ── Step 5: DTW score + shape grade + axis RMSE ───────────────────────
        # Single call — no double computation.
        score, global_rmse, axis_rmse, ref_centered, pat_centered =             calculate_mdtw_with_sensitivity(ref_data, pat_data, sensitivity)
        shape_grade = get_shape_grade(global_rmse, shape_tolerance)

        # ── Step 6: SPARC smoothness analysis ────────────────────────────────
        analyzer      = MovementAnalyzer()
        sparc_metrics = analyzer.compare_performances(ref_data, pat_data, use_filter=True)

        ref_s = sparc_metrics["Reference"]
        pat_s = sparc_metrics["Patient"]

        def _sparc_grade(pat_val, ref_val):
            if ref_val == 0: return 0
            ratio = pat_val / ref_val
            if ratio >= 1.00: return 10
            if ratio >= 0.95: return 9
            if ratio >= 0.85: return 8
            if ratio >= 0.70: return 7
            if ratio >= 0.50: return 6
            return 0

        sparc_grade_total = _sparc_grade(pat_s["Total_SPARC"],    ref_s["Total_SPARC"])
        sparc_grade_low   = _sparc_grade(pat_s["Low_Band_SPARC"], ref_s["Low_Band_SPARC"])
        sparc_grade_high  = _sparc_grade(pat_s["High_Band_SPARC"],ref_s["High_Band_SPARC"])

        # ── Step 7: Report ────────────────────────────────────────────────────
        report_text = generate_therapist_report(
            rom_ratio=rom_ratio,
            avg_rom_grade=avg_rom_grade,
            rom_axis_grades=rom_axis_grades,
            rom_ratios=rom_ratios,
            global_rmse=global_rmse,
            shape_grade=shape_grade,
            axis_rmse=axis_rmse,
            shape_limit=shape_tolerance,
        )

        # Get patient feedback from SPARC for the JSON response
        from score import print_patient_feedback
        patient_feedback = print_patient_feedback(sparc_metrics)
        
        # Keep therapist report separate from patient feedback
        full_report = report_text

        # ── Step 8: Plot ──────────────────────────────────────────────────────
        plot_b64 = build_comparison_figure(
            ref_centered, pat_centered, score, full_report,
            sparc_metrics=sparc_metrics,
        )

        # ── Step 9: Encode Excel file to base64 ──────────────────────────────
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
            "sparc": {
                "total":              round(float(pat_s["Total_SPARC"]),              4),
                "low_band":           round(float(pat_s["Low_Band_SPARC"]),           4),
                "high_band":          round(float(pat_s["High_Band_SPARC"]),          4),
                "velocity_rmse":      round(float(pat_s["Velocity_RMSE"]),            4),
                "peak_velocity":      round(float(pat_s["Peak_Velocity"]),            4),
                "ref_total":          round(float(ref_s["Total_SPARC"]),              4),
                "velocity_lag_frames":int(pat_s["Velocity_Peak_Lag_Frames"]),
                "velocity_lag_seconds":round(float(pat_s["Velocity_Peak_Lag_Seconds"]), 3),
                "sudden_peak_count":  int(pat_s["Sudden_Peak_Count"]),
                "sudden_drop_count":  int(pat_s["Sudden_Drop_Count"]),
            },
            "sparc_grades": {
                "total":      sparc_grade_total,
                "choppiness": sparc_grade_low,
                "tremor":     sparc_grade_high,
            },
            "patient_feedback": patient_feedback,
            "report_text":      full_report,
            "plot_image_b64":   plot_b64,
            "patient_file":     patient_file,
            "template_file":    template_file,
            "excel_file_b64":   excel_b64,
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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


@app.route("/templates", methods=["GET"])
def list_templates_route():
    if not os.path.exists(TEMPLATES_FOLDER):
        return jsonify({"templates": []})
    files = sorted(
        [f for f in os.listdir(TEMPLATES_FOLDER) if f.endswith(".xlsx")],
        reverse=True
    )
    return jsonify({"templates": files})


@app.route("/mocap/unity_start", methods=["POST"])
def mocap_unity_start():
    global _mocap_process, _mocap_status
    if _mocap_process and _mocap_process.poll() is None:
        return jsonify({"error": "Recording already in progress"}), 400

    data     = request.get_json(force=True)
    duration = float(data.get("duration", 8))
    grace    = float(data.get("grace",    6))
    exercise = data.get("exercise", "exercise")
    arm      = data.get("arm",      MOCAP_ARM)
    trail    = data.get("trail",    "trail_1")

    _mocap_status = {
        "state":   "grace",
        "message": f"Launching Unity Game and Camera...",
        "output_file": None,
    }
    
    unity_exe_path = os.path.join(os.path.dirname(__file__), "UnityPipeline", "Builds", "Body control 3D model.exe")
    
    def run():
        global _mocap_process, _mocap_status
        unity_proc = None
        if os.path.exists(unity_exe_path):
            unity_proc = subprocess.Popen([unity_exe_path])
        else:
            print(f"[Unity Launch] Warning: Executable not found at {unity_exe_path}")
            
        try:
            env = os.environ.copy()
            env["MOCAP_CAMERA"]      = "realsense"
            env["MOCAP_ARM"]         = arm
            env["MOCAP_DURATION"]    = str(duration)
            env["MOCAP_GRACE"]       = str(grace)
            env["MOCAP_EXERCISE"]    = exercise
            env["MOCAP_TRAIL"]       = trail
            env["MOCAP_OUTPUT_DIR"]  = OUTPUT_FOLDER
            env["MOCAP_MODEL_PATH"]  = MOCAP_MODEL_PATH
            env["MOCAP_UDP_STREAM"]  = "true"

            _mocap_process = subprocess.Popen(
                [sys.executable, MOCAP_SCRIPT],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = _mocap_process.communicate()

            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")

            if stdout_str.strip():
                print("[MOCAP STDOUT]\n" + stdout_str)
            if stderr_str.strip():
                print("[MOCAP STDERR]\n" + stderr_str)

            excel_saved_in_stdout = "Excel Saved" in stdout_str
            output_file = latest_file_in(OUTPUT_FOLDER)
            succeeded = (
                _mocap_process.returncode == 0
                or (excel_saved_in_stdout and output_file is not None)
            )

            if succeeded:
                _mocap_status = {
                    "state":       "done",
                    "message":     "Unity Session Recording complete. Ready to analyze.",
                    "output_file": os.path.basename(output_file) if output_file else None,
                    "stdout":      stdout_str,
                    "stderr":      stderr_str,
                }
            else:
                _mocap_status = {
                    "state":   "error",
                    "message": "Unity pipeline failed or cancelled.",
                    "stdout":  stdout_str,
                    "stderr":  stderr_str,
                }
        except Exception as e:
            _mocap_status = {
                "state":   "error",
                "message": f"Server crash: {str(e)}"
            }
        finally:
            if unity_proc:
                try:
                    unity_proc.terminate()
                    unity_proc.wait(timeout=2)
                except:
                    try:
                        unity_proc.kill()
                    except:
                        pass

    threading.Thread(target=run).start()
    return jsonify({"status": "starting", "message": "Starting Unity game & motion capture..."})


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
