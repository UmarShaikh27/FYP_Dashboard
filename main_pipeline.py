"""
main_pipeline.py  –  Master orchestrator for the unified physiotherapy pipeline.

This script ties together all pipeline stages in order:
    1. Capture  (optional – gesture-enabled live capture, or use existing file)
    2. Normalize (shoulder-relative + arm-length normalization)
    3. Segment Attempts (velocity-based rest-gap detection)
    4. Per-attempt loop:
       4a. Filter  (3-stage signal cleaning)
       4b. Scale Template (to patient's global coords)
       4c. Score   (hierarchical weighted DTW + SPARC scoring)
    5. Session Plots (overlay all extracted attempts)
    6. Global Report (weighted averages across attempts + summary)

CONFIGURATION
─────────────
Edit the variables in the "=== Configuration ===" section below to
match your patient / trail / file paths, or set them programmatically.

USAGE
─────
    cd "Capstone 2 Environment"
    python main_pipeline.py

Or open this file in a Jupyter environment and run cell-by-cell.
"""

import os
import sys
import json
import cv2
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

# ── Ensure the Capstone 2 Environment folder is on sys.path ────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# ═══════════════════════════════════════════════════════════════════════════
#  === Configuration ===
# ═══════════════════════════════════════════════════════════════════════════
PATIENT_NAME       = "Shahmir"
ARM                = "auto"

# Exercise selection – change this to select the exercise
# Options: "eight_tracing", "circumduction", "flexion_2kg"
EXERCISE_TYPE      = "eight_tracing"

# Multi-attempt configuration ──────────────────────────────────────────
N_ATTEMPTS             = None   # Set to None for auto-detection, or an int (e.g. 5)
CAPTURE_DURATION_TOTAL = 20    # total capture duration in seconds (None triggers console prompt)
MIN_GAP_SECONDS        = 1.5    # minimum frames required to be a valid rest (now heavily robust due to smoothing)
REST_VELOCITY_PERCENTILE = 18.0 # Valleys are around the bottom ~15-30% of frames
MIN_ATTEMPT_SECONDS    = 0.5    # Attempts are only ~2.5 seconds long, so 3.0s is too strict

GRACE_PERIOD       = 5         # seconds before recording starts
VELOCITY_BUFFER_PCT = 0.10     # ±10% of template length as velocity lead/lag tolerance

# Gesture control ──────────────────────────────────────────────────────
GESTURE_ENABLED        = True
GESTURE_HOLD_SECONDS   = 2.0

# Paths ─────────────────────────────────────────────────────────────────────
#   Output directory for this patient
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output_excel", PATIENT_NAME)

#   The raw capture file (skip capture step and use this directly)
#   Set to None to run the live capture module instead.
# RAW_CAPTURE_PATH = r"C:\Users\LOQ\Desktop\Uni Work\FYP\Depth Data\MediaPipe Dataset\Physiotherapy Grading\right_arm_motion_eigth short rom_test.xlsx"


RAW_CAPTURE_PATH = None

#   The normalised template file – the "gold standard" reference
#   that has wrist_normalized_x/y/z columns.
TEMPLATE_NORMALIZED_PATH = os.path.join(
    SCRIPT_DIR,
    "templates",
    "8_tracing_right_wrist_template.xlsx"
)

#   Scoring weights config file
WEIGHTS_PATH = os.path.join(SCRIPT_DIR, "scoring_weights", "scoring_weights_eight_tracing.json")


# ═══════════════════════════════════════════════════════════════════════════
#  === Pipeline Stages ===
# ═══════════════════════════════════════════════════════════════════════════

def get_exercise_paths(exercise_type: str) -> tuple[str, str]:
    """Map exercise type to template and weights paths."""
    exercise_map = {
        "eight_tracing": (
            os.path.join(SCRIPT_DIR, "templates", "8_tracing_right_wrist_template.xlsx"),
            os.path.join(SCRIPT_DIR, "scoring_weights", "scoring_weights_eight_tracing.json")
        ),
        "circumduction": (
            os.path.join(SCRIPT_DIR, "templates", "Circumduction_right_wrist_template.xlsx"),
            os.path.join(SCRIPT_DIR, "scoring_weights", "scoring_weights_circumduction.json")
        ),
        "flexion_2kg": (
            os.path.join(SCRIPT_DIR, "templates", "Flexion_2kg_right_wrist_template.xlsx"),
            os.path.join(SCRIPT_DIR, "scoring_weights", "scoring_weights_flexion.json")
        ),
    }
    return exercise_map.get(exercise_type, ("", ""))


def stage_1_capture(session_num: int = 1):
    """Live motion capture with gesture support for hand arm selection + start."""
    from capture import run_capture

    print("\n" + "=" * 60)
    print("  STAGE 1 – LIVE CAPTURE (Gesture-Enabled)")
    print("=" * 60)
    raw_path, selected_arm = run_capture(
        patient_name=PATIENT_NAME,
        arm=ARM,
        duration=CAPTURE_DURATION_TOTAL,
        grace_period=GRACE_PERIOD,
        exercise_type=EXERCISE_TYPE,
        output_dir=OUTPUT_DIR,
        session=session_num,
        gesture_enabled=GESTURE_ENABLED,
        gesture_hold_seconds=GESTURE_HOLD_SECONDS,
    )
    return raw_path, selected_arm


def stage_2_normalize(raw_path: str) -> str:
    """Add shoulder-relative + normalised columns."""
    from normalize import normalize

    print("\n" + "=" * 60)
    print("  STAGE 2 – NORMALIZE (Bone Transform)")
    print("=" * 60)
    return normalize(raw_path, OUTPUT_DIR)


def stage_3_segment(normalized_path: str) -> list:
    """Segment the normalized recording into N_ATTEMPTS slices."""
    from segment_attempts import segment_attempts

    print("\n" + "=" * 60)
    print("  STAGE 3 – SEGMENT ATTEMPTS")
    print("=" * 60)
    return segment_attempts(
        normalized_excel_path=normalized_path,
        output_dir=OUTPUT_DIR,
        n_attempts=N_ATTEMPTS,
        min_gap_seconds=MIN_GAP_SECONDS,
        rest_velocity_percentile=REST_VELOCITY_PERCENTILE,
        min_attempt_seconds=MIN_ATTEMPT_SECONDS,
        exercise_type=EXERCISE_TYPE,
    )


def stage_4_filter(normalized_path: str, output_dir: str) -> str:
    """Apply the 3-stage filtering pipeline."""
    from filter_data import filter_motion

    print("\n" + "=" * 60)
    print("  STAGE 4 – FILTER")
    print("=" * 60)
    return filter_motion(normalized_path, output_dir)


def stage_5_scale_template(normalized_path: str, output_dir: str) -> str:
    """Scale the reference template to the patient's global coords."""
    from scale_template import scale

    print("\n" + "=" * 60)
    print("  STAGE 5 – SCALE TEMPLATE")
    print("=" * 60)
    return scale(
        template_path=TEMPLATE_NORMALIZED_PATH,
        patient_normalized_path=normalized_path,
        output_dir=output_dir,
    )


def stage_6_score(filtered_path: str, scaled_template_path: str,
                  output_dir: str, weights: dict) -> dict:
    """Score patient vs scaled template using DTW + SPARC."""
    from score import score_movement

    print("\n" + "=" * 60)
    print("  STAGE 6 – SCORE (Hierarchical Weighted)")
    print("=" * 60)
    return score_movement(
        patient_filtered_path=filtered_path,
        template_scaled_path=scaled_template_path,
        output_dir=output_dir,
        velocity_buffer_pct=VELOCITY_BUFFER_PCT,
        weights=weights,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  === Session-Level Outputs ===
# ═══════════════════════════════════════════════════════════════════════════

def plot_session_attempts(slice_paths: list, output_dir: str) -> str:
    """
    Plot all extracted attempts overlaid on one figure.
    Shows X/Y/Z per-axis traces + 3D trajectory for each attempt.
    """
    n = len(slice_paths)
    colors = plt.cm.tab10(np.linspace(0, 1, max(n, 10)))

    fig = plt.figure(figsize=(18, 12))
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.30)

    # 3D trajectory
    ax3d = fig.add_subplot(gs[0, 0], projection="3d")
    # Per-axis
    ax_x = fig.add_subplot(gs[0, 1])
    ax_y = fig.add_subplot(gs[1, 0])
    ax_z = fig.add_subplot(gs[1, 1])

    axes_per_axis = [ax_x, ax_y, ax_z]
    axis_labels = ["X", "Y", "Z"]
    norm_cols = ["wrist_normalized_x", "wrist_normalized_y", "wrist_normalized_z"]

    for i, path in enumerate(slice_paths):
        try:
            df = pd.read_excel(path)
        except Exception:
            continue

        label = f"Attempt {i+1}"
        c = colors[i % len(colors)]

        if all(col in df.columns for col in norm_cols):
            x = df[norm_cols[0]].values
            y = df[norm_cols[1]].values
            z = df[norm_cols[2]].values

            ax3d.plot(x, y, z, color=c, linewidth=1.5, label=label, alpha=0.8)

            for j, ax in enumerate(axes_per_axis):
                vals = [x, y, z][j]
                ax.plot(vals, color=c, linewidth=1.2, label=label, alpha=0.8)

    ax3d.set_title("3D Trajectory — All Attempts", fontsize=13, fontweight="bold")
    ax3d.set_xlabel("X"); ax3d.set_ylabel("Y"); ax3d.set_zlabel("Z")
    ax3d.legend(fontsize=8, loc="upper left")

    for j, ax in enumerate(axes_per_axis):
        ax.set_title(f"{axis_labels[j]}-Axis — All Attempts", fontsize=11, fontweight="bold")
        ax.set_ylabel("Normalized Position")
        ax.set_xlabel("Frame")
        ax.legend(fontsize=8, loc="best")

    fig.suptitle(f"Session Attempts Overview — {PATIENT_NAME}",
                 fontsize=16, fontweight="bold", y=0.98)
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out_path = os.path.join(output_dir, "session_attempts_plot.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] Saved session attempts plot: {out_path}")
    return out_path


def plot_session_velocity(normalized_excel_path: str, output_dir: str) -> str:
    """Read the normalized Excel, compute per-frame 3D velocity, and save a plot."""
    df = pd.read_excel(normalized_excel_path)
    x = df["wrist_normalized_x"].values
    y = df["wrist_normalized_y"].values
    z = df["wrist_normalized_z"].values
    
    dx = np.diff(x, prepend=x[0])
    dy = np.diff(y, prepend=y[0])
    dz = np.diff(z, prepend=z[0])
    
    velocity = np.sqrt(dx**2 + dy**2 + dz**2)
    velocity[0] = 0.0
    
    # Same smoothing as the segmentation algorithm
    smoothed_velocity = pd.Series(velocity).rolling(window=15, center=True, min_periods=1).mean().values
    
    fig, ax = plt.subplots(figsize=(15, 6))
    ax.plot(velocity, color="#bdc3c7", linewidth=1.0, alpha=0.7, label="Raw Velocity Jitter")
    ax.plot(smoothed_velocity, color="#e74c3c", linewidth=2.0, label="Smoothed Velocity (Used for Segmentation)")
    ax.set_title("Full Session - 3D Wrist Velocity", fontsize=14, fontweight="bold")
    ax.set_xlabel("Frame", fontsize=12)
    ax.set_ylabel("Velocity (m/frame)", fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.legend()
    
    out_path = os.path.join(output_dir, "session_velocity_plot.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] Saved full session velocity plot: {out_path}")
    return out_path


def generate_session_summary_excel(attempt_results: list, weights: dict,
                                   output_dir: str) -> str:
    """
    Create session_summary.xlsx with all attempt scores + weighted averages.
    """
    rows = []
    for i, r in enumerate(attempt_results):
        rows.append({
            "attempt": i + 1,
            "global_score": r["global_score"],
            "dtw_score": r["dtw_score"],
            "som_grade": r["som_grade"],
            "rom_grade": r["rom_grade"],
            "tempo_control_grade": r["tempo_control_grade"],
            "hesitation_grade": r["hesitation_grade"],
            "tremor_grade": r["tremor_grade"],
            "global_rmse": r["global_rmse"],
            "rom_ratio_avg": r["rom_ratio_avg"],
        })

    df = pd.DataFrame(rows)

    # Compute averages
    avg_row = {"attempt": "AVERAGE"}
    for col in df.columns:
        if col == "attempt":
            continue
        avg_row[col] = round(float(df[col].mean()), 2)

    # Compute weighted global average
    from score import weighted_average
    avg_scores = {
        "som": avg_row["som_grade"],
        "rom": avg_row["rom_grade"],
        "tempo_control": avg_row["tempo_control_grade"],
        "hesitation": avg_row["hesitation_grade"],
        "tremor": avg_row["tremor_grade"],
    }
    weighted_global = weighted_average(avg_scores, weights)
    weighted_dtw = weighted_average(
        {"som": avg_row["som_grade"], "rom": avg_row["rom_grade"]}, weights
    )
    weighted_sparc = weighted_average(
        {"tempo_control": avg_row["tempo_control_grade"],
         "hesitation": avg_row["hesitation_grade"],
         "tremor": avg_row["tremor_grade"]}, weights
    )

    avg_row["global_score"] = weighted_global
    avg_row["dtw_score"] = weighted_dtw
    avg_row["sparc_score"] = weighted_sparc

    # Append average row
    avg_df = pd.DataFrame([avg_row])
    df = pd.concat([df, avg_df], ignore_index=True)

    # Weights sheet
    weights_df = pd.DataFrame([weights])

    out_path = os.path.join(output_dir, "session_summary.xlsx")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="scores", index=False)
        weights_df.to_excel(writer, sheet_name="weights", index=False)

    print(f"[OK] Saved session summary: {out_path}")
    return out_path


def plot_global_report(attempt_results: list, weights: dict,
                       output_dir: str) -> str:
    """
    Generate a visual global report showing per-attempt scores and overall averages.
    """
    from score import weighted_average

    n = len(attempt_results)
    attempt_nums = list(range(1, n + 1))

    # Extract per-attempt scores
    global_scores = [r["global_score"] for r in attempt_results]
    dtw_scores = [r["dtw_score"] for r in attempt_results]
    som_grades = [r["som_grade"] for r in attempt_results]
    rom_grades = [r["rom_grade"] for r in attempt_results]
    tempo_control_grades = [r["tempo_control_grade"] for r in attempt_results]
    hesit_grades = [r["hesitation_grade"] for r in attempt_results]
    tremor_grades = [r["tremor_grade"] for r in attempt_results]

    # Compute a proxy SPARC score per attempt for the bar chart
    sparc_scores = [
        weighted_average(
            {"tempo_control": r["tempo_control_grade"], "hesitation": r["hesitation_grade"], "tremor": r["tremor_grade"]},
            weights
        ) for r in attempt_results
    ]

    # Compute weighted averages
    avg_scores = {
        "som": np.mean(som_grades),
        "rom": np.mean(rom_grades),
        "tempo_control": np.mean(tempo_control_grades),
        "hesitation": np.mean(hesit_grades),
        "tremor": np.mean(tremor_grades),
    }
    weighted_global = weighted_average(avg_scores, weights)
    weighted_dtw = weighted_average(
        {"som": avg_scores["som"], "rom": avg_scores["rom"]}, weights
    )
    weighted_sparc = weighted_average(
        {"tempo_control": avg_scores["tempo_control"],
         "hesitation": avg_scores["hesitation"],
         "tremor": avg_scores["tremor"]}, weights
    )

    fig = plt.figure(figsize=(18, 14))
    gs = fig.add_gridspec(3, 2, hspace=0.45, wspace=0.35,
                          height_ratios=[1, 1.2, 1.5])

    # ── Row 0: Overall global score ────────────────────────────────────
    ax_header = fig.add_subplot(gs[0, :])
    ax_header.axis("off")
    ax_header.text(0.5, 0.80, f"GLOBAL PATIENT REPORT — {PATIENT_NAME}",
                   ha="center", va="center", fontsize=22, fontweight="bold")
    color = "#2ecc71" if weighted_global >= 7 else "#f39c12" if weighted_global >= 4 else "#e74c3c"
    ax_header.text(0.5, 0.35, f"Overall Weighted Score: {weighted_global:.1f} / 10",
                   ha="center", va="center", fontsize=36, fontweight="bold", color=color)
    ax_header.text(0.5, 0.05,
                   f"DTW: {weighted_dtw:.1f}  |  SPARC: {weighted_sparc:.1f}  |  "
                   f"Attempts: {n}",
                   ha="center", va="center", fontsize=14, color="#555555")

    # ── Row 1 left: Per-attempt global scores (line + bar) ─────────────
    ax_line = fig.add_subplot(gs[1, 0])
    bar_colors = ["#2ecc71" if s >= 7 else "#f39c12" if s >= 4 else "#e74c3c"
                  for s in global_scores]
    ax_line.bar(attempt_nums, global_scores, color=bar_colors, alpha=0.7, width=0.6)
    ax_line.plot(attempt_nums, global_scores, "ko-", linewidth=2, markersize=8)
    ax_line.axhline(y=weighted_global, color="blue", linestyle="--", linewidth=2,
                    label=f"Weighted Avg: {weighted_global:.1f}")
    ax_line.set_xlabel("Attempt", fontsize=12)
    ax_line.set_ylabel("Global Score (0-10)", fontsize=12)
    ax_line.set_title("Per-Attempt Global Scores", fontsize=13, fontweight="bold")
    ax_line.set_ylim(0, 11)
    ax_line.set_xticks(attempt_nums)
    ax_line.legend(fontsize=10)
    for i, s in enumerate(global_scores):
        ax_line.text(attempt_nums[i], s + 0.3, f"{s:.1f}", ha="center",
                     fontsize=10, fontweight="bold")

    # ── Row 1 right: DTW vs SPARC breakdown per attempt ────────────────
    ax_comp = fig.add_subplot(gs[1, 1])
    x_pos = np.arange(n)
    width = 0.35
    ax_comp.bar(x_pos - width/2, dtw_scores, width, label="DTW Score",
                color="#3498db", alpha=0.8)
    ax_comp.bar(x_pos + width/2, sparc_scores, width, label="SPARC Score",
                color="#e67e22", alpha=0.8)
    ax_comp.axhline(y=weighted_dtw, color="#3498db", linestyle="--",
                    linewidth=1.5, alpha=0.7)
    ax_comp.axhline(y=weighted_sparc, color="#e67e22", linestyle="--",
                    linewidth=1.5, alpha=0.7)
    ax_comp.set_xlabel("Attempt", fontsize=12)
    ax_comp.set_ylabel("Score (0-10)", fontsize=12)
    ax_comp.set_title("DTW vs SPARC per Attempt", fontsize=13, fontweight="bold")
    ax_comp.set_ylim(0, 11)
    ax_comp.set_xticks(x_pos)
    ax_comp.set_xticklabels([str(i+1) for i in range(n)])
    ax_comp.legend(fontsize=10)

    # ── Row 2: Full sub-component breakdown (grouped bar) ─────────────
    ax_sub = fig.add_subplot(gs[2, :])
    sub_labels = ["SoM", "ROM", "Tempo Ctrl", "Hesitation", "Tremor"]
    sub_data = {
        "SoM": som_grades, "ROM": rom_grades,
        "Tempo Ctrl": tempo_control_grades, "Hesitation": hesit_grades,
        "Tremor": tremor_grades,
    }
    sub_avgs = [np.mean(sub_data[k]) for k in sub_labels]
    
    # Map label names to weight keys
    weight_key_map = {
        "SoM": "som", "ROM": "rom", "Tempo Ctrl": "tempo_control",
        "Hesitation": "hesitation", "Tremor": "tremor",
    }

    x_sub = np.arange(len(sub_labels))
    total_width = 0.8
    bar_width = total_width / n if n > 0 else 0.4

    for i in range(n):
        offsets = x_sub - total_width/2 + bar_width * i + bar_width / 2
        vals = [sub_data[k][i] for k in sub_labels]
        ax_sub.bar(offsets, vals, bar_width, label=f"Attempt {i+1}", alpha=0.7)

    # Overlay average markers
    for j, avg_val in enumerate(sub_avgs):
        wk = weight_key_map[sub_labels[j]]
        w_val = weights.get(wk, 0.1)
        ax_sub.plot(j, avg_val, "kD", markersize=10, zorder=5)
        ax_sub.annotate(f"Avg:{avg_val:.1f}\nw={w_val}",
                        (j, avg_val), textcoords="offset points",
                        xytext=(0, 15), ha="center", fontsize=8, fontweight="bold")

    ax_sub.set_xlabel("Sub-Component", fontsize=12)
    ax_sub.set_ylabel("Score (0-10)", fontsize=12)
    ax_sub.set_title("Sub-Component Breakdown Across Attempts", fontsize=13, fontweight="bold")
    ax_sub.set_ylim(0, 12)
    ax_sub.set_xticks(x_sub)
    ax_sub.set_xticklabels(sub_labels, fontsize=11)
    ax_sub.legend(fontsize=8, loc="upper right", ncol=min(n, 5))

    fig.suptitle("", fontsize=1)  # clear
    plt.tight_layout()

    out_path = os.path.join(output_dir, "global_report.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[OK] Saved global report: {out_path}")
    return out_path


# ═══════════════════════════════════════════════════════════════════════════
#  === Main ===
# ═══════════════════════════════════════════════════════════════════════════
def main():
    global EXERCISE_TYPE, TEMPLATE_NORMALIZED_PATH, WEIGHTS_PATH, ARM, OUTPUT_DIR
    
    # ── Set exercise paths based on EXERCISE_TYPE configuration ──
    TEMPLATE_NORMALIZED_PATH, WEIGHTS_PATH = get_exercise_paths(EXERCISE_TYPE)

    # ── Dynamically create hierarchy: outputs -> patient -> exercise -> session_XX ──
    base_exercise_dir = os.path.join(SCRIPT_DIR, "outputs", PATIENT_NAME, EXERCISE_TYPE)
    os.makedirs(base_exercise_dir, exist_ok=True)
    
    existing_sessions = []
    for d in os.listdir(base_exercise_dir):
        if d.startswith("session_") and os.path.isdir(os.path.join(base_exercise_dir, d)):
            try:
                existing_sessions.append(int(d.split("session_")[1]))
            except ValueError:
                pass
    
    current_session = max(existing_sessions) + 1 if existing_sessions else 1
    OUTPUT_DIR = os.path.join(base_exercise_dir, f"session_{current_session}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── Load scoring weights ───────────────────────────────────────────
    from score import load_weights
    weights = load_weights(WEIGHTS_PATH)

    print("\n" + "=" * 60)
    print("  CAPSTONE 2 — INTEGRATED PHYSIOTHERAPY PIPELINE")
    print("=" * 60)
    print(f"  Patient:    {PATIENT_NAME}")
    print(f"  Arm:        {ARM}")
    print(f"  Exercise:   {EXERCISE_TYPE}")
    print(f"  Session:    {current_session}")
    print(f"  N_ATTEMPTS: {N_ATTEMPTS if N_ATTEMPTS else 'Auto-detect'}")
    print(f"  Weights:    {json.dumps(weights)}")
    print(f"  Output:     {OUTPUT_DIR}")
    print("=" * 60)

    # ── Stage 1: Capture ───────────────────────────────────────────────
    if RAW_CAPTURE_PATH is None:
        raw_path, ARM = stage_1_capture(session_num=current_session)
    else:
        raw_path = RAW_CAPTURE_PATH
        print(f"\n[INFO] Skipping live capture – using existing file: {raw_path}")

    # ── Stage 2: Normalize ─────────────────────────────────────────────
    normalized_path = stage_2_normalize(raw_path)
    
    # ── Plot overall session velocity ──────────────────────────────────
    plot_session_velocity(normalized_path, OUTPUT_DIR)

    # ── Stage 3: Segment ───────────────────────────────────────────────
    slice_paths = stage_3_segment(normalized_path)
    actual_attempts = len(slice_paths)
    print(f"\n[INFO] Detected/requested {actual_attempts} attempt(s)")

    # ── Plot all attempts overlaid ─────────────────────────────────────
    session_plot_path = plot_session_attempts(slice_paths, OUTPUT_DIR)

    # ── Loop over attempts ──────────────────────────────────────────────
    attempt_results = []
    for i, attempt_path in enumerate(slice_paths):
        attempt_num = i + 1
        print(f"\n{'#' * 60}")
        print(f"  PROCESSING ATTEMPT {attempt_num} / {actual_attempts}")
        print(f"{'#' * 60}")

        attempt_output_dir = os.path.join(OUTPUT_DIR, f"attempt_{attempt_num}")
        os.makedirs(attempt_output_dir, exist_ok=True)

        # ── Stage 4: Filter ──────────────────────────────────────────
        filtered_path = stage_4_filter(attempt_path, attempt_output_dir)

        # ── Stage 5: Scale template ──────────────────────────────────
        # Use full normalized_path for arm length from entire session
        scaled_template_path = stage_5_scale_template(normalized_path, attempt_output_dir)

        # ── Stage 6: Score ───────────────────────────────────────────
        result = stage_6_score(filtered_path, scaled_template_path,
                               attempt_output_dir, weights)
        attempt_results.append(result)

    # ── Generate session summary Excel ─────────────────────────────────
    summary_path = generate_session_summary_excel(attempt_results, weights, OUTPUT_DIR)

    # ── Generate global report plot ────────────────────────────────────
    global_report_path = plot_global_report(attempt_results, weights, OUTPUT_DIR)

    # ── Compute final averages for console output ──────────────────────
    from score import weighted_average

    avg_scores = {
        "som": np.mean([r["som_grade"] for r in attempt_results]),
        "rom": np.mean([r["rom_grade"] for r in attempt_results]),
        "tempo_control": np.mean([r["tempo_control_grade"] for r in attempt_results]),
        "hesitation": np.mean([r["hesitation_grade"] for r in attempt_results]),
        "tremor": np.mean([r["tremor_grade"] for r in attempt_results]),
    }
    weighted_global = weighted_average(avg_scores, weights)
    weighted_dtw = weighted_average(
        {"som": avg_scores["som"], "rom": avg_scores["rom"]}, weights
    )
    weighted_sparc = weighted_average(
        {"tempo_control": avg_scores["tempo_control"],
         "hesitation": avg_scores["hesitation"],
         "tremor": avg_scores["tremor"]}, weights
    )

    avg_rmse = np.mean([r["global_rmse"] for r in attempt_results])

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  PIPELINE COMPLETE — SESSION SUMMARY")
    print("=" * 70)
    print(f"  Patient:          {PATIENT_NAME}")
    print(f"  Attempts scored:  {actual_attempts}")
    print(f"  Output folder:    {OUTPUT_DIR}")
    print("─" * 70)
    print(f"  {'Attempt':<10} {'Global':>8} {'DTW':>8} {'SPARC':>8} "
          f"{'SoM':>6} {'ROM':>6} {'TCtrl':>6} {'Hes':>6} {'Trem':>6}")
    print("─" * 70)
    
    for i, r in enumerate(attempt_results):
        r_sparc = weighted_average(
            {"tempo_control": r["tempo_control_grade"], "hesitation": r["hesitation_grade"], "tremor": r["tremor_grade"]},
            weights
        )
        print(f"  {i+1:<10} {r['global_score']:>8.1f} {r['dtw_score']:>8.1f} "
              f"{r_sparc:>8.1f} {r['som_grade']:>6} {r['rom_grade']:>6} "
              f"{r['tempo_control_grade']:>6.1f} {r['hesitation_grade']:>6.1f} "
              f"{r['tremor_grade']:>6.1f}")
    print("─" * 70)
    print(f"  {'WEIGHTED':>10} {weighted_global:>8.1f} {weighted_dtw:>8.1f} "
          f"{weighted_sparc:>8.1f} {avg_scores['som']:>6.1f} {avg_scores['rom']:>6.1f} "
          f"{avg_scores['tempo_control']:>6.1f} {avg_scores['hesitation']:>6.1f} "
          f"{avg_scores['tremor']:>6.1f}")
    print("─" * 70)
    print(f"  Avg DTW RMSE:     {avg_rmse:.4f} m")
    print(f"  Avg SPARC scores: TempoCtrl={avg_scores['tempo_control']:.1f} "
          f"Hesitation={avg_scores['hesitation']:.1f} "
          f"Tremor={avg_scores['tremor']:.1f}")
    print("=" * 70)
    print(f"\n  📊 Session attempts plot: {session_plot_path}")
    print(f"  📋 Session summary:       {summary_path}")
    print(f"  📈 Global report:          {global_report_path}")

    return {
        "attempts": attempt_results,
        "weighted_global_score": weighted_global,
        "weighted_dtw_score": weighted_dtw,
        "weighted_sparc_score": weighted_sparc,
        "avg_scores": avg_scores,
        "avg_rmse": avg_rmse,
        "weights": weights,
        "saved": {
            "session_attempts_plot": session_plot_path,
            "session_summary": summary_path,
            "global_report": global_report_path,
        },
    }


if __name__ == "__main__":
    main()
