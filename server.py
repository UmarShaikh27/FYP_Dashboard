"""
server.py - PhysioSync Local Backend (Updated with Capstone 2 Multi-Attempt Pipeline)
=====================================================================================

Integrated Pipeline (Main → Analyze):
  1. Capture           - gesture-enabled live capture (optional, from Capstone 2)
  2. Normalize         - shoulder-relative + arm-length normalization
  3. Segment Attempts  - velocity-based multi-attempt extraction
  4. Per-Attempt Loop:
     4a. Filter       - 3-stage signal cleaning
     4b. Scale Template
     4c. Score        - hierarchical weighted DTW + SPARC
  5. Session Aggregation - weighted averages + global report

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
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import sys

# All pipeline modules are now at the root folder
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Import pipeline modules
from capture import run_capture
from normalize import normalize
from segment_attempts import segment_attempts
from filter_data import filter_motion
from scale_template import scale as scale_template_file
from score import (
    score_movement,
    load_weights,
    weighted_average,
    _extract_patient_global_trajectory_from_filtered,
    _require_columns,
    TEMPLATE_COLS,
)

# Import legacy score helpers (backward compatibility)
from score import (
    calculate_mdtw_with_sensitivity,
    calculate_rom_metrics,
    get_rom_grade,
    get_shape_grade,
    generate_therapist_report,
    MovementAnalyzer,
)

app = Flask(__name__)
CORS(app)

# ============================================================
# CONFIGURATION
# ============================================================
MOCAP_ARM        = "right"   # Default arm if not specified by client
# ============================================================

OUTPUT_FOLDER    = os.path.join(ROOT_DIR, "output_excel")
TEMPLATES_FOLDER = os.path.join(ROOT_DIR, "templates")
SCORING_WEIGHTS  = os.path.join(ROOT_DIR, "scoring_weights")

# Keep CAPSTONE_WEIGHTS as alias for existing code that references it
CAPSTONE_WEIGHTS = SCORING_WEIGHTS

_mocap_process = None
_mocap_status  = {"state": "idle", "message": "", "output_file": None}
_pipeline_state = {"state": "idle", "message": "", "progress": 0}

# ── Utilities ─────────────────────────────────────────────────────────────────

def latest_file_in(folder, extension=".xlsx"):
    """Returns the most recently modified file with given extension."""
    if not os.path.exists(folder):
        return None
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
    """Read an Excel file and convert it to base64 string."""
    try:
        with open(excel_file_path, 'rb') as f:
            excel_data = f.read()
        b64 = base64.b64encode(excel_data).decode("utf-8")
        print(f"[Excel] Converted to base64: {os.path.basename(excel_file_path)} ({len(excel_data)} bytes)")
        return b64
    except Exception as e:
        print(f"[Excel] Failed to encode: {e}")
        return None


def normalize_exercise_type(exercise_type):
    """
    Map user-facing exercise names to the Capstone 2 internal keys.
    e.g. "Eight Tracing" -> "eight_tracing"
    """
    mapping = {
        "eight tracing":  "eight_tracing",
        "eight_tracing":  "eight_tracing",
        "circumduction":  "circumduction",
        "flexion":        "flexion",
        "flexion_2kg":    "flexion",
        "flexion 2kg":    "flexion",
    }
    key = exercise_type.lower().strip() if exercise_type else "eight_tracing"
    return mapping.get(key, key.lower().replace(" ", "_"))


def get_exercise_weights(exercise_type):
    """Load exercise-specific scoring weights."""
    normalized = normalize_exercise_type(exercise_type)
    # Try the normalized key first, then with _eight_tracing suffix for the naming convention
    candidates = [
        os.path.join(CAPSTONE_WEIGHTS, f"scoring_weights_{normalized}.json"),
        os.path.join(CAPSTONE_WEIGHTS, f"scoring_weights_{normalized.replace('_', '_')}.json"),
    ]
    for weights_path in candidates:
        if os.path.exists(weights_path):
            return load_weights(weights_path)
    print(f"[WARN] Weights file not found for '{exercise_type}' (normalized: '{normalized}'), using defaults")
    # Fall back to the eight_tracing weights as default
    fallback = os.path.join(CAPSTONE_WEIGHTS, "scoring_weights_eight_tracing.json")
    if os.path.exists(fallback):
        return load_weights(fallback)
    return load_weights(None)


def build_comparison_figure(ref_centered, pat_centered, score, report_text, sparc_metrics=None):
    """Dark-themed plot with 3D trajectory and SPARC analysis."""
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


# ── Helper: Generate comparison plot ───────────────────────────────────────
def generate_comparison_plot(first_attempt, all_attempts, exercise_type="eight_tracing"):
    """Generate a simple matplotlib plot for multi-attempt comparison."""
    try:
        fig = plt.figure(figsize=(12, 4))
        fig.patch.set_facecolor("#0f1419")
        
        # Plot 1: Per-attempt scores
        ax1 = fig.add_subplot(1, 2, 1)
        scores = [a.get("score", 0) for a in all_attempts]
        attempts = list(range(1, len(scores) + 1))
        ax1.plot(attempts, scores, marker='o', linestyle='-', linewidth=2, markersize=8, color='#00d4ff')
        ax1.fill_between(attempts, scores, alpha=0.3, color='#0090ff')
        ax1.set_xlabel('Attempt #', color='#e8edf5')
        ax1.set_ylabel('Score', color='#e8edf5')
        ax1.set_title('Score Progression', color='#e8edf5', fontsize=12, fontweight='bold')
        ax1.set_facecolor('#1a202c')
        ax1.tick_params(colors='#e8edf5')
        ax1.grid(True, alpha=0.2, color='#232a3a')
        for spine in ax1.spines.values():
            spine.set_color('#232a3a')
        
        # Plot 2: Weighted components
        ax2 = fig.add_subplot(1, 2, 2)
        components = list(first_attempt.get("weighted_components", {}).keys()) or ["SOM", "ROM", "Tremor", "Hesitation", "Control"]
        values = list(first_attempt.get("weighted_components", {}).values()) or [0] * len(components)
        colors = ['#00d4ff', '#0090ff', '#00b894', '#fbbf24', '#ff6b6e'][:len(components)]
        bars = ax2.barh(components, values, color=colors, alpha=0.8)
        ax2.set_xlabel('Score', color='#e8edf5')
        ax2.set_title('Weighted Components (First Attempt)', color='#e8edf5', fontsize=12, fontweight='bold')
        ax2.set_facecolor('#1a202c')
        ax2.tick_params(colors='#e8edf5')
        ax2.set_xlim([0, 10])
        for spine in ax2.spines.values():
            spine.set_color('#232a3a')
        
        # Add value labels on bars
        for bar in bars:
            width = bar.get_width()
            ax2.text(width, bar.get_y() + bar.get_height()/2, f'{width:.1f}', 
                    ha='left', va='center', color='#e8edf5', fontsize=9, fontweight='bold')
        
        fig.tight_layout()
        
        # Convert to base64
        buf = io.BytesIO()
        fig.savefig(buf, format='png', facecolor='#0f1419', dpi=100, bbox_inches='tight')
        buf.seek(0)
        image_b64 = base64.b64encode(buf.read()).decode()
        plt.close(fig)
        return image_b64
    except Exception as e:
        print(f"[ERROR] Failed to generate plot: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════════════

def run_multi_attempt_analysis(patient_file, template_file, exercise_type="eight_tracing", 
                               n_attempts=None, weights=None):
    """
    Complete multi-attempt analysis pipeline.
    
    Returns dict with:
    {
      "global_score": float,
      "num_attempts": int,
      "per_attempt_scores": list,
      "weighted_scores": dict,
      "session_summary": str,
      "per_attempt_details": list,
      "session_plot": "base64_png"
    }
    """
    global _pipeline_state
    
    pat_path = os.path.join(OUTPUT_FOLDER, patient_file)
    ref_path = os.path.join(TEMPLATES_FOLDER, template_file)
    
    if not os.path.exists(pat_path):
        raise FileNotFoundError(f"Patient file not found: {patient_file}")
    if not os.path.exists(ref_path):
        ref_path = os.path.join(TEMPLATES_FOLDER, template_file)
        if not os.path.exists(ref_path):
            raise FileNotFoundError(f"Template file not found in templates/: {template_file}")
    
    # Get weights
    if weights is None:
        weights = get_exercise_weights(exercise_type)
    
    # ── Temp working directory ──────────────────────────────────────────────
    temp_dir = os.path.join(OUTPUT_FOLDER, "_temp_pipeline")
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # ── Stage 1: Normalize ───────────────────────────────────────────────
        _pipeline_state = {"state": "normalizing", "message": "Normalizing bone transform...", "progress": 10}
        normalized_path = normalize(pat_path, temp_dir)
        print(f"[OK] Normalized: {normalized_path}")
        
        # ── Stage 2: Segment Attempts ───────────────────────────────────────
        _pipeline_state = {"state": "segmenting", "message": "Detecting exercise attempts...", "progress": 20}
        attempt_paths = segment_attempts(
            normalized_excel_path=normalized_path,
            output_dir=temp_dir,
            n_attempts=n_attempts,
            min_gap_seconds=1.5,
            rest_velocity_percentile=18.0,
            min_attempt_seconds=0.5,
            exercise_type=exercise_type
        )
        num_attempts = len(attempt_paths)
        print(f"[OK] Segmented into {num_attempts} attempts")
        
        # ── Stage 3: Per-Attempt Analysis ───────────────────────────────────
        per_attempt_scores = []
        per_attempt_details = []
        
        for i, attempt_path in enumerate(attempt_paths):
            attempt_num = i + 1
            _pipeline_state = {
                "state": "analyzing",
                "message": f"Analyzing attempt {attempt_num}/{num_attempts}...",
                "current_attempt": attempt_num,
                "total_attempts": num_attempts,
                "progress": 30 + (i * 60 // num_attempts)
            }
            
            # Create attempt-specific output directory
            attempt_out_dir = os.path.join(temp_dir, f"attempt_{attempt_num}")
            os.makedirs(attempt_out_dir, exist_ok=True)
            
            # Filter
            filtered_path = filter_motion(attempt_path, attempt_out_dir)
            
            # Scale template (use original normalized file for scaling params)
            scaled_template_path = scale_template_file(
                template_path=ref_path,
                patient_normalized_path=normalized_path,
                output_dir=attempt_out_dir
            )
            
            # Score this attempt
            attempt_result = score_movement(
                patient_filtered_path=filtered_path,
                template_scaled_path=scaled_template_path,
                output_dir=attempt_out_dir,
                velocity_buffer_pct=0.10,
                weights=weights
            )
            
            # Debug: Log what we got back
            print(f"[DEBUG] Attempt {attempt_num} result keys: {list(attempt_result.keys())}")
            
            sparc_block = attempt_result.get("sparc") or {}
            sparc_ref = sparc_block.get("reference") or {}
            sparc_pat = sparc_block.get("patient") or {}

            # Extract all fields directly from compute_score return (all out of 10)
            attempt_score = {
                "attempt_num":        attempt_num,
                "global_score":       attempt_result.get("global_score", 0),
                "dtw_score":          attempt_result.get("dtw_score", 0),
                "som_grade":          attempt_result.get("som_grade", 0),
                "rom_grade":          attempt_result.get("rom_grade", 0),
                "tempo_control_grade": attempt_result.get("tempo_control_grade", 0),
                "hesitation_grade":   attempt_result.get("hesitation_grade", 0),
                "tremor_grade":       attempt_result.get("tremor_grade", 0),
                "global_rmse":        attempt_result.get("global_rmse", 0),
                "rom_ratio_avg":      attempt_result.get("rom_ratio_avg", 0),
                "axis_rmse":          attempt_result.get("axis_rmse") or {},
                "rom_axis_grades":    attempt_result.get("rom_axis_grades") or {},
                "ref_peak_velocity":  sparc_ref.get("Peak_Velocity"),
                "pat_peak_velocity":  sparc_pat.get("Peak_Velocity"),
                "ref_mean_velocity":  sparc_ref.get("Mean_Velocity"),
                "pat_mean_velocity":  sparc_pat.get("Mean_Velocity"),
                "pat_velocity_rmse":  sparc_pat.get("Velocity_RMSE"),
                "plot_path":          attempt_result.get("saved", {}).get("therapist_view_png"),
                "patient_view_path":  attempt_result.get("saved", {}).get("patient_view_png"),
            }
            per_attempt_details.append(attempt_score)
            per_attempt_scores.append(attempt_score["global_score"])
        
        # ── Stage 4: Session Aggregation ────────────────────────────────────
        _pipeline_state = {"state": "aggregating", "message": "Calculating session averages...", "progress": 90}
        
        # Average all sub-scores across attempts
        def avg_field(field):
            vals = [a[field] for a in per_attempt_details if a.get(field) is not None]
            return round(float(np.mean(vals)), 2) if vals else 0.0

        session_scores = {
            "global_score":         avg_field("global_score"),
            "dtw_score":            avg_field("dtw_score"),
            "som_grade":            avg_field("som_grade"),
            "rom_grade":            avg_field("rom_grade"),
            "tempo_control_grade":  avg_field("tempo_control_grade"),
            "hesitation_grade":     avg_field("hesitation_grade"),
            "tremor_grade":         avg_field("tremor_grade"),
        }

        def _avg_nested_axis(details, path_key, subkeys=("X", "Y", "Z")):
            out = {}
            for k in subkeys:
                vals = []
                for a in details:
                    block = a.get(path_key) or {}
                    v = block.get(k)
                    if v is not None:
                        vals.append(float(v))
                out[k] = round(float(np.mean(vals)), 4) if vals else None
            return out

        session_axis_rmse = _avg_nested_axis(per_attempt_details, "axis_rmse")
        session_rom_axis_grades = {}
        for k in ("X", "Y", "Z"):
            vals = []
            for a in per_attempt_details:
                block = a.get("rom_axis_grades") or {}
                v = block.get(k)
                if v is not None:
                    vals.append(float(v))
            session_rom_axis_grades[k] = int(round(float(np.mean(vals)))) if vals else None

        def _avg_optional_scalar(field):
            vals = [a[field] for a in per_attempt_details if a.get(field) is not None]
            return round(float(np.mean(vals)), 4) if vals else None

        session_ref_peak = _avg_optional_scalar("ref_peak_velocity")
        session_pat_peak = _avg_optional_scalar("pat_peak_velocity")
        session_ref_mean = _avg_optional_scalar("ref_mean_velocity")
        session_pat_mean = _avg_optional_scalar("pat_mean_velocity")
        session_pat_vel_rmse = _avg_optional_scalar("pat_velocity_rmse")

        best_score  = max(per_attempt_scores)
        worst_score = min(per_attempt_scores)
        improvement = per_attempt_scores[-1] - per_attempt_scores[0] if num_attempts > 1 else 0
        
        # ── Stage 5: Generate Session Plots ─────────────────────────────────
        _pipeline_state = {"state": "plotting", "message": "Generating session plots...", "progress": 95}

        def encode_image(path):
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            return ""

        # Import session-level plot functions from main_pipeline.py
        try:
            from main_pipeline import plot_session_attempts, plot_global_report
            session_attempts_plot_path = plot_session_attempts(attempt_paths, temp_dir)
            session_attempts_plot_b64  = encode_image(session_attempts_plot_path)
            global_report_plot_b64     = encode_image(
                plot_global_report(per_attempt_details, weights, temp_dir)
            )
        except Exception as plot_err:
            print(f"[WARN] Could not generate session plots: {plot_err}")
            session_attempts_plot_b64 = ""
            global_report_plot_b64    = ""

        attempt_progression = {
            "avg_score":                round(float(np.mean(per_attempt_scores)), 2),
            "best_attempt":             best_score,
            "worst_attempt":            worst_score,
            "improvement_first_to_last": round(improvement, 2),
            "trend": "improving" if improvement > 0.5 else "declining" if improvement < -0.5 else "stable"
        }
        
        return {
            # Session-level scores (all out of 10)
            "global_score":             session_scores["global_score"],
            "dtw_score":                session_scores["dtw_score"],
            "som_grade":                session_scores["som_grade"],
            "rom_grade":                session_scores["rom_grade"],
            "tempo_control_grade":      session_scores["tempo_control_grade"],
            "hesitation_grade":         session_scores["hesitation_grade"],
            "tremor_grade":             session_scores["tremor_grade"],

            "axis_rmse":                session_axis_rmse,
            "rom_axis_grades":          session_rom_axis_grades,
            "ref_peak_velocity":        session_ref_peak,
            "pat_peak_velocity":        session_pat_peak,
            "ref_mean_velocity":        session_ref_mean,
            "pat_mean_velocity":        session_pat_mean,
            "pat_velocity_rmse":        session_pat_vel_rmse,

            # Attempt summary
            "num_attempts":             num_attempts,
            "per_attempt_scores":       per_attempt_scores,   # list of floats out of 10
            "per_attempt_metrics":      per_attempt_details,  # full detail per attempt
            "attempt_progression":      attempt_progression,

            # Plots (base64 PNG)
            "session_attempts_plot_b64": session_attempts_plot_b64,
            "global_report_plot_b64":    global_report_plot_b64,

            # Meta
            "exercise_type":            exercise_type,
            "weights_config":           weights,
            "patient_file":             os.path.basename(patient_file) if patient_file else "",
            "template_file":            os.path.basename(template_file) if template_file else "",
        }
    
    except Exception as e:
        _pipeline_state = {"state": "error", "message": str(e), "progress": 0}
        raise


# ═══════════════════════════════════════════════════════════════════════════
#  Flask Routes
# ═══════════════════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/templates", methods=["GET"])
def get_templates():
    """List all available exercise template files."""
    try:
        if not os.path.exists(TEMPLATES_FOLDER):
            return jsonify({"templates": []})
        templates = [f for f in os.listdir(TEMPLATES_FOLDER) if f.endswith(('.xlsx', '.xls'))]
        return jsonify({"templates": sorted(templates)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/files/patient", methods=["GET"])
def get_patient_files():
    """List all recorded patient motion files."""
    try:
        if not os.path.exists(OUTPUT_FOLDER):
            return jsonify({"files": []})
        files = [f for f in os.listdir(OUTPUT_FOLDER) if f.endswith(('.xlsx', '.xls')) and not f.startswith('_temp')]
        return jsonify({"files": sorted(files, reverse=True)})  # Most recent first
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/pipeline/status", methods=["GET"])
def pipeline_status():
    """Get current pipeline state and progress."""
    return jsonify(_pipeline_state)


@app.route("/pipeline/analyze", methods=["POST"])
def pipeline_analyze():
    """
    Full multi-attempt analysis.
    
    Body: {
      "patient_file": "...",
      "template_file": "...",
      "exercise_type": "eight_tracing",
      "n_attempts": null,
      "weights_override": null
    }
    """
    data = request.get_json(force=True)
    
    patient_file = data.get("patient_file")
    template_file = data.get("template_file")
    exercise_type_raw = data.get("exercise_type", "eight_tracing")
    exercise_type = normalize_exercise_type(exercise_type_raw)  # "Eight Tracing" -> "eight_tracing"
    n_attempts = data.get("n_attempts")
    weights_override = data.get("weights_override")
    
    print(f"[INFO] Exercise type: '{exercise_type_raw}' -> normalized: '{exercise_type}'")
    
    if not patient_file or not template_file:
        return jsonify({"error": "patient_file and template_file required"}), 400
    
    try:
        weights = weights_override if weights_override else get_exercise_weights(exercise_type)
        
        result = run_multi_attempt_analysis(
            patient_file=patient_file,
            template_file=template_file,
            exercise_type=exercise_type,
            n_attempts=n_attempts,
            weights=weights
        )
        
        return jsonify(result)
    
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route("/mocap/start", methods=["POST"])
def mocap_start():
    """
    Launches capture.py's run_capture() in a background thread.

    Body: {
      "duration": 8,
      "grace": 6,
      "exercise": "eight_tracing",
      "arm": "right",
      "gesture_enabled": false
    }
    """
    global _mocap_status

    if _mocap_status.get("state") == "recording":
        return jsonify({"error": "Recording already in progress"}), 400

    data = request.get_json(force=True)
    duration        = float(data.get("duration", 8))
    grace           = float(data.get("grace", 5))
    exercise        = data.get("exercise", "exercise")
    arm             = data.get("arm", MOCAP_ARM)
    gesture_enabled = bool(data.get("gesture_enabled", False))

    _mocap_status = {
        "state": "recording",
        "message": f"Camera opening — press SPACE in the camera window to start…",
        "output_file": None,
    }

    def run():
        global _mocap_status
        try:
            raw_path, selected_arm = run_capture(
                patient_name="patient",
                arm=arm,
                duration=duration,
                grace_period=int(grace),
                exercise_type=exercise,
                output_dir=OUTPUT_FOLDER,
                session=1,
                gesture_enabled=gesture_enabled,
                gesture_hold_seconds=2.0,
                camera_source="realsense",
            )
            _mocap_status = {
                "state": "done",
                "message": "Recording complete. Ready to analyze.",
                "output_file": os.path.basename(raw_path),
            }
        except Exception as e:
            import traceback
            _mocap_status = {
                "state": "error",
                "message": str(e),
                "full_stderr": traceback.format_exc(),
                "output_file": None,
            }

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/mocap/unity_start", methods=["POST"])
def mocap_unity_start():
    """
    Launch the gamified (Unity) session.

    - Opens the Unity game executable.
    - Runs headless motion capture (no CV2 window) via unity_bridge.py.
    - Waits for Unity to send START_EXERCISE before recording begins.
    - Sends countdown timer ticks to Unity every 0.5 s during recording.
    - On completion, runs the full scoring pipeline and sends results to Unity.

    Body: {
      "duration": 20,
      "exercise": "eight_tracing",
      "arm": "right"
    }
    """
    global _mocap_status, unity_bridge_instance, unity_process

    if _mocap_status.get("state") in ("recording", "analyzing"):
        # Let's forcefully reset it to allow a new session
        print("[Server] Forcefully overriding previous stuck mocap session.")
        if globals().get('unity_bridge_instance') is not None:
            print("[Server] Closing old bridge instance...")
            unity_bridge_instance.close()
            unity_bridge_instance = None
        if globals().get('unity_process') is not None:
            print("[Server] Terminating old Unity process...")
            try:
                unity_process.terminate()
            except Exception:
                pass
            unity_process = None
        _mocap_status = {"state": "idle", "message": "Ready", "output_file": None}

    data     = request.get_json(force=True)
    duration = float(data.get("duration", 20))
    exercise = normalize_exercise_type(data.get("exercise", "eight_tracing"))
    arm      = data.get("arm", MOCAP_ARM)

    _mocap_status = {
        "state":       "recording",
        "message":     "Unity session starting\u2026 waiting for game to boot.",
        "output_file": None,
    }

    def run():
        global _mocap_status
        bridge = None
        try:
            # 1. Launch Unity executable (non-blocking)
            unity_exe = os.path.join(ROOT_DIR, "UnityPipeline", "Builds", "Body control 3D model.exe")
            if os.path.exists(unity_exe):
                global unity_process
                unity_process = subprocess.Popen([unity_exe])
                print(f"[Unity] Launched: {unity_exe}")
                import time as _time
                _time.sleep(3)   # give Unity time to boot and bind its socket
            else:
                print(f"[Unity] WARNING: exe not found at {unity_exe}")

            # 2. Headless motion capture (blocks until done)
            from unity_bridge import UnityBridgeCapture
            global unity_bridge_instance
            unity_bridge_instance = UnityBridgeCapture(
                arm=arm,
                duration=duration,
                exercise_type=exercise,
                output_dir=OUTPUT_FOLDER,
                camera_source="realsense",
            )
            raw_path = unity_bridge_instance.run()   # blocks until duration elapsed

            _mocap_status = {
                "state":       "analyzing",
                "message":     "Recording complete. Running analysis pipeline\u2026",
                "output_file": os.path.basename(raw_path),
            }

            # 3. Full scoring pipeline (same as normal pipeline)
            template_file = _template_for_exercise(exercise)
            weights       = get_exercise_weights(exercise)
            result        = run_multi_attempt_analysis(
                patient_file=os.path.basename(raw_path),
                template_file=template_file,
                exercise_type=exercise,
                weights=weights,
            )

            # 4. Notify Unity session is done (it will show "Session Complete!" and auto-close)
            unity_bridge_instance.send_done()
            unity_bridge_instance.close()
            unity_bridge_instance = None

            _mocap_status = {
                "state":       "done",
                "message":     "Gamified session complete.",
                "output_file": os.path.basename(raw_path),
                "result":      result,
            }

        except Exception as e:
            import traceback
            _mocap_status = {
                "state":      "error",
                "message":    str(e),
                "full_stderr": traceback.format_exc(),
                "output_file": None,
            }
        finally:
            if bridge:
                bridge.close()

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"status": "started"})


def _template_for_exercise(exercise_type: str) -> str:
    """Map a normalised exercise key to its template filename."""
    mapping = {
        "eight_tracing": "8_tracing_right_wrist_template.xlsx",
        "circumduction":  "Circumduction_right_wrist_template.xlsx",
        "flexion":        "Flexion_2kg_right_wrist_template.xlsx",
        "flexion_2kg":    "Flexion_2kg_right_wrist_template.xlsx",
    }
    return mapping.get(exercise_type, "8_tracing_right_wrist_template.xlsx")


@app.route("/mocap/status", methods=["GET"])
def mocap_status():
    return jsonify(_mocap_status)


@app.route("/mocap/logs", methods=["GET"])
def mocap_logs():
    """Returns full stderr output from the last mocap run."""
    return jsonify({
        "full_stderr": _mocap_status.get("full_stderr", ""),
        "stdout": _mocap_status.get("stdout", ""),
        "state": _mocap_status.get("state", "idle"),
    })


@app.route("/mocap/stop", methods=["POST"])
def mocap_stop():
    """Reset mocap status (gamified sessions end automatically)."""
    global _mocap_status
    _mocap_status = {"state": "idle", "message": "", "output_file": None}
    return jsonify({"status": "reset"})


@app.route("/files/patient", methods=["GET"])
def list_patient_files():
    """Returns list of all recorded patient Excel files."""
    if not os.path.exists(OUTPUT_FOLDER):
        return jsonify([])
    files = [f for f in os.listdir(OUTPUT_FOLDER) if f.endswith(".xlsx")]
    return jsonify(files)

@app.route("/analyze", methods=["POST"])
def analyze_legacy():
    """
    Legacy single-attempt analysis endpoint.
    Now uses weighted scoring but processes single attempt only.
    """
    data = request.get_json(force=True)

    patient_file = data.get("patient_file")
    template_file = data.get("template_file")
    exercise_type = data.get("exercise_type", "eight_tracing")
    sensitivity = float(data.get("sensitivity", 3.0))
    shape_tolerance = float(data.get("shape_tolerance", 0.20))

    if not patient_file or not template_file:
        return jsonify({"error": "patient_file and template_file are required"}), 400

    pat_path = os.path.join(OUTPUT_FOLDER, patient_file)
    ref_path = os.path.join(TEMPLATES_FOLDER, template_file)

    if not os.path.exists(pat_path):
        return jsonify({"error": f"Patient file not found: {patient_file}"}), 404
    if not os.path.exists(ref_path):
        ref_path = os.path.join(TEMPLATES_FOLDER, template_file)
        if not os.path.exists(ref_path):
            return jsonify({"error": f"Template file not found: {template_file}"}), 404

    try:
        temp_dir = os.path.join(OUTPUT_FOLDER, "_temp_pipeline")
        os.makedirs(temp_dir, exist_ok=True)

        filtered_path = filter_motion(pat_path, temp_dir)
        scaled_template_path = scale_template_file(
            template_path=ref_path,
            patient_normalized_path=pat_path,
            output_dir=temp_dir,
        )

        # Get weights for this exercise
        weights = get_exercise_weights(exercise_type)

        # Use new scoring system
        result = score_movement(
            patient_filtered_path=filtered_path,
            template_scaled_path=scaled_template_path,
            output_dir=temp_dir,
            velocity_buffer_pct=0.10,
            weights=weights
        )

        return jsonify({
            "score": result["final_score"],
            "global_score": result["final_score"],  # New field
            "global_rmse": result["global_rmse"],
            "axis_rmse": result.get("axis_rmse", {}),
            "rom_ratio": result.get("rom_ratio", 0),
            "rom_ratios": result.get("rom_ratios", {}),
            "rom_axis_grades": result.get("rom_axis_grades", []),
            "avg_rom_grade": result.get("avg_rom_grade", 0),
            "shape_grade": result.get("shape_grade", 0),
            "sparc": result.get("sparc", {}),
            "weighted_components": result.get("weighted_components", {}),
            "report_text": result.get("report_text", ""),
            "plot_image_b64": result.get("plot_image_b64", ""),
            "exercise_type": exercise_type,
            "num_attempts": 1  # Legacy single-attempt
        })

    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


if __name__ == "__main__":
    print("=" * 60)
    print("PhysioSync Local Backend")
    print("=" * 60)
    print(f"Output folder:    {OUTPUT_FOLDER}")
    print(f"Templates folder: {TEMPLATES_FOLDER}")
    print(f"Scoring weights:  {SCORING_WEIGHTS}")
    print(f"Capture module:   capture.py (in-process, gesture_enabled=False)")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=True)
