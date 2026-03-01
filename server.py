"""
server.py — PhysioSync Local Backend
=====================================
Runs on the therapist's machine alongside the React frontend.
Exposes REST API to trigger mocap recording, run DTW analysis,
and return results to be saved in Firebase.

Install dependencies:
  pip install flask flask-cors numpy pandas tslearn openpyxl matplotlib mediapipe opencv-python scipy

Run:
  python server.py

The React frontend calls this at http://localhost:5050
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import subprocess
import threading
import os
import io
import base64
import json
import time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend — no GUI window, returns image as bytes
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

# ── Import your own scripts ─────────────────────────────────────────────────
# Place server.py in the SAME folder as mocap_script.py and disected_mmDTW.py
import sys
sys.path.insert(0, os.path.dirname(__file__))

from disected_mmDTW import (
    extract_hand_data,
    calculate_mdtw_with_sensitivity,
    calculate_rom_metrics,
    get_rom_grade,
    get_shape_grade,
    generate_therapist_report,
)

app = Flask(__name__)
CORS(app)  # Allow requests from React dev server (localhost:5173) and Vercel

# ── Configuration — edit these paths ────────────────────────────────────────
MOCAP_SCRIPT   = os.path.join(os.path.dirname(__file__), "mocap_script.py")
OUTPUT_FOLDER  = os.path.join(os.path.dirname(__file__), "output_excel")
TEMPLATES_FOLDER = os.path.join(os.path.dirname(__file__), "templates")

# Global state for the currently running mocap process
_mocap_process = None
_mocap_status  = {"state": "idle", "message": "", "output_file": None}

# ── Utility ──────────────────────────────────────────────────────────────────

def latest_file_in(folder, extension=".xlsx"):
    """Returns the most recently modified file with the given extension."""
    files = [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.endswith(extension)
    ]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def figure_to_base64(fig):
    """Converts a matplotlib Figure to a base64-encoded PNG string."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120, facecolor="#0a0d12")
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return img_b64


def build_comparison_figure(ref_data, pat_data, score, report_text):
    """
    Builds the 3D comparison plot and returns it as a base64 PNG.
    Dark-themed to match the PhysioSync UI.
    """
    fig = plt.figure(figsize=(14, 7), facecolor="#0a0d12")

    # --- 3D Trajectory ---
    ax1 = fig.add_subplot(1, 2, 1, projection="3d")
    ax1.set_facecolor("#111520")
    ax1.plot(ref_data[:, 0], ref_data[:, 1], ref_data[:, 2],
             color="#23395B", linestyle="--", linewidth=1.5, label="Expert Ref")
    ax1.plot(pat_data[:, 0], pat_data[:, 1], pat_data[:, 2],
             color="#00e5c3", linewidth=2.5, label="Patient")
    ax1.set_title(f"Score: {score}/100", color="#00e5c3", fontsize=15, fontweight="bold", pad=12)
    ax1.legend(facecolor="#1a2030", labelcolor="#e8edf5", edgecolor="#232a3a")
    ax1.set_xlabel("X", color="#6b7a96")
    ax1.set_ylabel("Y", color="#6b7a96")
    ax1.set_zlabel("Z", color="#6b7a96")
    ax1.tick_params(colors="#6b7a96")
    for spine in ax1.spines.values():
        spine.set_edgecolor("#232a3a")

    # --- Report Text ---
    ax2 = fig.add_subplot(1, 2, 2)
    ax2.set_facecolor("#111520")
    ax2.axis("off")
    ax2.text(0.04, 0.98, "THERAPIST ANALYTICS", color="#00e5c3",
             fontsize=11, fontweight="bold", va="top", transform=ax2.transAxes)
    ax2.text(0.04, 0.90, report_text, color="#e8edf5",
             fontsize=9, family="monospace", va="top", transform=ax2.transAxes,
             linespacing=1.5)

    fig.tight_layout(pad=2.0)
    return figure_to_base64(fig)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """Frontend pings this to confirm the local server is running."""
    return jsonify({"status": "ok", "version": "1.0"})


@app.route("/templates", methods=["GET"])
def list_templates():
    """Returns available exercise template files for the therapist to pick."""
    if not os.path.exists(TEMPLATES_FOLDER):
        os.makedirs(TEMPLATES_FOLDER)
    files = [f for f in os.listdir(TEMPLATES_FOLDER) if f.endswith(".xlsx")]
    return jsonify({"templates": files})


@app.route("/mocap/start", methods=["POST"])
def mocap_start():
    """
    Launches mocap_script.py as a subprocess.
    Body: { "duration": 8 }   (seconds)
    """
    global _mocap_process, _mocap_status

    if _mocap_process and _mocap_process.poll() is None:
        return jsonify({"error": "Recording already in progress"}), 400

    data     = request.get_json(force=True)
    duration = int(data.get("duration", 8))

    _mocap_status = {"state": "recording", "message": f"Recording for {duration}s…", "output_file": None}

    def run():
        global _mocap_process, _mocap_status
        try:
            env = os.environ.copy()
            env["RECORDING_DURATION"] = str(duration)
            _mocap_process = subprocess.Popen(
                [sys.executable, MOCAP_SCRIPT],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = _mocap_process.communicate()
            if _mocap_process.returncode == 0:
                output_file = latest_file_in(OUTPUT_FOLDER)
                _mocap_status = {
                    "state": "done",
                    "message": "Recording complete.",
                    "output_file": os.path.basename(output_file) if output_file else None,
                }
            else:
                _mocap_status = {
                    "state": "error",
                    "message": stderr.decode("utf-8", errors="replace")[:500],
                    "output_file": None,
                }
        except Exception as e:
            _mocap_status = {"state": "error", "message": str(e), "output_file": None}

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/mocap/status", methods=["GET"])
def mocap_status():
    """Frontend polls this every second during recording."""
    return jsonify(_mocap_status)


@app.route("/mocap/stop", methods=["POST"])
def mocap_stop():
    """Sends SIGTERM to the mocap subprocess (triggers save in finally block)."""
    global _mocap_process
    if _mocap_process and _mocap_process.poll() is None:
        _mocap_process.terminate()
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not_running"})


@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Runs DTW analysis on a recorded file vs a template.
    Body:
    {
      "patient_file":   "pose_20260228_182339.xlsx",   // in output_excel/
      "template_file":  "right_wrist_template.xlsx",   // in templates/
      "sensitivity":    3.0,
      "shape_tolerance": 0.20
    }
    Returns full analytics + base64 plot image.
    """
    data = request.get_json(force=True)

    patient_file   = data.get("patient_file")
    template_file  = data.get("template_file")
    sensitivity    = float(data.get("sensitivity", 3.0))
    shape_tolerance = float(data.get("shape_tolerance", 0.20))

    if not patient_file or not template_file:
        return jsonify({"error": "patient_file and template_file are required"}), 400

    pat_path = os.path.join(OUTPUT_FOLDER, patient_file)
    ref_path = os.path.join(TEMPLATES_FOLDER, template_file)

    if not os.path.exists(pat_path):
        return jsonify({"error": f"Patient file not found: {patient_file}"}), 404
    if not os.path.exists(ref_path):
        return jsonify({"error": f"Template file not found: {template_file}"}), 404

    try:
        ref_data = extract_hand_data(ref_path)
        pat_data = extract_hand_data(pat_path)

        # Core analysis
        score, global_rmse, axis_rmse = calculate_mdtw_with_sensitivity(
            ref_data, pat_data, sensitivity
        )
        rom_ratio, rom_ratios = calculate_rom_metrics(ref_data, pat_data)

        rom_axis_grades = [get_rom_grade(r) for r in rom_ratios]
        avg_rom_grade   = float(np.mean(rom_axis_grades))
        shape_grade     = get_shape_grade(global_rmse, shape_tolerance)

        report_text = generate_therapist_report(
            rom_ratio, avg_rom_grade, rom_axis_grades, rom_ratios,
            global_rmse, shape_grade, axis_rmse, shape_tolerance
        )

        # Build dark-themed plot
        plot_b64 = build_comparison_figure(ref_data, pat_data, score, report_text)

        result = {
            "score":          score,
            "global_rmse":    round(float(global_rmse), 4),
            "axis_rmse":      {
                "x": round(float(axis_rmse[0]), 4),
                "y": round(float(axis_rmse[1]), 4),
                "z": round(float(axis_rmse[2]), 4),
            },
            "rom_ratio":      round(float(rom_ratio), 4),
            "rom_ratios":     {
                "x": round(float(rom_ratios[0]), 4),
                "y": round(float(rom_ratios[1]), 4),
                "z": round(float(rom_ratios[2]), 4),
            },
            "rom_axis_grades":  rom_axis_grades,
            "avg_rom_grade":    round(avg_rom_grade, 2),
            "shape_grade":      shape_grade,
            "report_text":      report_text,
            "plot_image_b64":   plot_b64,   # PNG embedded as base64
            "patient_file":     patient_file,
            "template_file":    template_file,
        }

        return jsonify(result)

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/files/patient", methods=["GET"])
def list_patient_files():
    """Lists recorded xlsx files so the therapist can pick one to analyze."""
    if not os.path.exists(OUTPUT_FOLDER):
        return jsonify({"files": []})
    files = sorted(
        [f for f in os.listdir(OUTPUT_FOLDER) if f.endswith(".xlsx")],
        reverse=True  # Newest first
    )
    return jsonify({"files": files})


if __name__ == "__main__":
    print("=" * 55)
    print("  PhysioSync Local Backend  —  http://localhost:5050")
    print("=" * 55)
    print(f"  Templates folder : {TEMPLATES_FOLDER}")
    print(f"  Output folder    : {OUTPUT_FOLDER}")
    print()
    print("  PUT your template .xlsx files inside: templates/")
    print("  Recorded files will appear in:        output_excel/")
    print("=" * 55)
    os.makedirs(TEMPLATES_FOLDER, exist_ok=True)
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    app.run(host="0.0.0.0", port=5050, debug=False)
