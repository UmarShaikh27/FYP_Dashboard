import React from 'react';

const KPI_DETAILS = [
  {
    key: 'som',
    title: 'SoM (Smoothness of Movement)',
    description:
      'SoM is derived from shape-matching error between the patient trajectory and the reference trajectory after temporal alignment.',
    how:
      'The pipeline mean-centers both trajectories, aligns them with constrained mDTW (Sakoe-Chiba), computes global RMSE along the optimal warping path, then maps RMSE to a 0-10 grade using calibrated thresholds.',
    formula: 'SoM = shape grade from global RMSE(mDTW-aligned trajectory error)',
    subMetrics: [
      'Axis-wise shape error (RMSE X/Y/Z): per-axis mDTW-aligned trajectory error.',
      'Largest axis RMSE helps localize directional mismatch (e.g., depth control vs horizontal control).',
    ],
  },
  {
    key: 'rom',
    title: 'ROM (Range of Motion)',
    description:
      'ROM evaluates whether movement amplitude matches the expected template amplitude across X, Y, and Z axes.',
    how:
      'For each axis, ratio = peak-to-peak(patient) / peak-to-peak(reference). Each axis ratio is converted to a grade using rule-based thresholds, then averaged to produce the final ROM grade.',
    formula: 'ROM = mean(grade_x, grade_y, grade_z), where grade_i is thresholded from ratio_i',
    subMetrics: [
      'ROM axis grades (X/Y/Z): each axis receives an independent grade before aggregation.',
      'Axis-level grades reveal directional ROM deficits that can be masked in the single ROM grade.',
    ],
  },
  {
    key: 'tempo',
    title: 'Tempo Control',
    description:
      'Tempo Control measures timing consistency and speed-profile similarity to the reference.',
    how:
      'The patient speed profile is compared with the reference speed profile (after resampling), velocity RMSE is computed, and this RMSE is mapped to a 0-10 grade using threshold bands.',
    formula: 'Tempo Control = grade(velocity RMSE between reference and patient speed profiles)',
    subMetrics: [
      'Velocity RMSE: core mismatch metric between patient and reference velocity profiles.',
      'Peak velocity (reference vs patient): compares maximum speed capability and control.',
      'Mean velocity (reference vs patient): compares average pace over the movement window.',
    ],
  },
  {
    key: 'hesitation',
    title: 'Hesitation',
    description:
      'Hesitation captures stop-and-go or choppy movement behavior in lower-frequency smoothness dynamics.',
    how:
      'SPARC low-band (0-5 Hz) is computed for both reference and patient. The absolute difference is then mapped to a 0-10 hesitation grade.',
    formula: 'Hesitation = grade(|LowBandSPARC_patient - LowBandSPARC_reference|)',
  },
  {
    key: 'tremor',
    title: 'Tremor',
    description:
      'Tremor captures high-frequency jitter or shake-like oscillations in movement.',
    how:
      'SPARC high-band (approximately 5-20 Hz) is computed for both reference and patient. The absolute difference is mapped to a 0-10 tremor grade.',
    formula: 'Tremor = grade(|HighBandSPARC_patient - HighBandSPARC_reference|)',
  },
];

export default function ScoringMethodology() {
  return (
    <div className="records-view">
      <div className="progress-table-container">
        <div className="progress-table-header">
          <div>
            <h1 className="progress-table-title">Scoring Methodology</h1>
            <p className="records-subtitle">
              High-level explanation of KPI and global score computation from the scoring pipeline.
            </p>
          </div>
        </div>

        <div className="chart-card">
          <div className="chart-card-title">Global Score</div>
          <p style={{ color: '#cbd5e1', marginTop: 0 }}>
            The global score is a weighted average of the five KPI grades on a 0-10 scale.
          </p>
          <div className="score-hover-copy" style={{ marginBottom: 0 }}>
            <strong>Formula:</strong> Global Score = weighted average of (SoM, ROM, Tempo Control, Hesitation, Tremor)
          </div>
          <p style={{ color: '#94a3b8', marginTop: 12, marginBottom: 0 }}>
            Exact weights are exercise-specific and configurable, so this page intentionally explains the computation flow without fixed constants.
          </p>
        </div>

        {KPI_DETAILS.map((kpi) => (
          <div key={kpi.key} className="chart-card">
            <div className="chart-card-title">{kpi.title}</div>
            <p style={{ color: '#cbd5e1', marginTop: 0 }}>{kpi.description}</p>
            <p style={{ color: '#cbd5e1', marginBottom: 8 }}>
              <strong>How it is computed:</strong> {kpi.how}
            </p>
            <div className="score-hover-copy" style={{ marginBottom: 0 }}>
              <strong>Formula:</strong> {kpi.formula}
            </div>
            {Array.isArray(kpi.subMetrics) && kpi.subMetrics.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <p style={{ color: '#cbd5e1', margin: '0 0 6px 0', fontSize: 13 }}>
                  <strong>Detailed metrics under this KPI:</strong>
                </p>
                <ul style={{ margin: 0, paddingLeft: 18, color: '#cbd5e1' }}>
                  {kpi.subMetrics.map((item) => (
                    <li key={item} style={{ marginBottom: 4, fontSize: 13 }}>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
