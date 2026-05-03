/**
 * ProgressTable.jsx - Records page with Analysis and Progress tabs.
 *
 * This component maintains the existing record table while introducing:
 * - Patient-specific header + tabs
 * - Analysis Results with expandable score ring + KPI cards
 * - Per-attempt details with ordered figures
 * - Progress Report with exercise filtering and trend charts
 */

import React, { useState, useMemo, useEffect } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import {
  LineChart,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import './ProgressTable.css';

const KPI_KEYS = [
  { key: 'som_grade', label: 'SoM', color: '#00d4ff' },
  { key: 'rom_grade', label: 'ROM', color: '#22c55e' },
  { key: 'tempo_control_grade', label: 'Tempo', color: '#fbbf24' },
  { key: 'hesitation_grade', label: 'Hesitation', color: '#fb7185' },
  { key: 'tremor_grade', label: 'Tremor', color: '#f97316' },
];

const TAB_KEYS = {
  ANALYSIS: 'analysis',
  PROGRESS: 'progress',
};

const scoreColor = (value) => {
  const n = Number(value);
  if (n >= 7) return '#00e5c3';
  if (n >= 4) return '#f39c12';
  return '#ff4b6e';
};

const formatDate = (ts) => {
  if (!ts) return 'N/A';
  const d = ts.toDate ? ts.toDate() : new Date(ts);
  return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const getDateValue = (rec) => {
  if (!rec?.createdAt) return 0;
  return rec.createdAt.toDate ? rec.createdAt.toDate().getTime() : new Date(rec.createdAt).getTime();
};

const getDurationLabel = (rec) => {
  if (rec.durationMinutes) return `${rec.durationMinutes} min`;
  if (rec.duration) return `${rec.duration}s`;
  if (rec.recordingDuration) return `${rec.recordingDuration}s`;
  return 'N/A';
};

const defaultExercises = (analyses) => {
  const names = analyses
    .map((rec) => rec.exerciseName || rec.exercise_type || 'Unknown')
    .filter(Boolean);
  return Array.from(new Set(names)).sort();
};

const renderClickableDot = ({ cx, cy, payload, stroke }) => (
  <circle
    cx={cx}
    cy={cy}
    r={5}
    fill={stroke}
    stroke="#111"
    strokeWidth={1}
    style={{ cursor: 'pointer' }}
    onClick={() => payload.__onClick && payload.__onClick(payload.id)}
  />
);

export default function ProgressTable({
  patient = null,
  sessions = [],
  analyses = [],
  analysisResults: legacyAnalysisResults = [],
  loading = false,
  onSelectPatient,
  onAnalysisDeleted,
  onSessionDeleted,
}) {
  const [activeTab, setActiveTab] = useState(TAB_KEYS.ANALYSIS);
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [detailsRows, setDetailsRows] = useState(new Set());
  const [selectedExercises, setSelectedExercises] = useState([]);

  const analysisResults = analyses.length ? analyses : legacyAnalysisResults;
  const exerciseOptions = useMemo(() => defaultExercises(analysisResults), [analysisResults]);

  useEffect(() => {
    if (exerciseOptions.length && selectedExercises.length === 0) {
      setSelectedExercises(exerciseOptions);
    }
  }, [exerciseOptions, selectedExercises.length]);

  const filteredAnalyses = useMemo(() => {
    if (!selectedExercises.length) return analysisResults;
    return analysisResults.filter((rec) => selectedExercises.includes(rec.exerciseName || rec.exercise_type || 'Unknown'));
  }, [analysisResults, selectedExercises]);

  const sortedAnalyses = useMemo(
    () => [...filteredAnalyses].sort((a, b) => getDateValue(a) - getDateValue(b)),
    [filteredAnalyses]
  );

  const chartData = useMemo(
    () => sortedAnalyses.map((rec, index) => ({
      id: rec.id,
      name: `S${index + 1}`,
      sessionLabel: rec.exerciseName || rec.exercise_type || `Session ${index + 1}`,
      createdAt: formatDate(rec.createdAt),
      global_score: Number(rec.global_score ?? rec.score ?? 0),
      som_grade: Number(rec.som_grade ?? 0),
      rom_grade: Number(rec.rom_grade ?? 0),
      tempo_control_grade: Number(rec.tempo_control_grade ?? 0),
      hesitation_grade: Number(rec.hesitation_grade ?? 0),
      tremor_grade: Number(rec.tremor_grade ?? 0),
      original: rec,
    })),
    [sortedAnalyses]
  );

  const tableAnalyses = useMemo(() => [...sortedAnalyses].reverse(), [sortedAnalyses]);

  const handleToggleRow = (id) => {
    const next = new Set(expandedRows);
    next.has(id) ? next.delete(id) : next.add(id);
    setExpandedRows(next);
  };

  const handleToggleDetails = (id) => {
    const next = new Set(detailsRows);
    next.has(id) ? next.delete(id) : next.add(id);
    setDetailsRows(next);
  };

  const handleExerciseToggle = (exercise) => {
    setSelectedExercises((prev) => {
      const next = new Set(prev);
      next.has(exercise) ? next.delete(exercise) : next.add(exercise);
      return Array.from(next);
    });
  };

  const handleSessionClick = (id) => {
    setActiveTab(TAB_KEYS.ANALYSIS);
    setExpandedRows(new Set([id]));
    setDetailsRows((prev) => new Set(prev).add(id));
    window.requestAnimationFrame(() => {
      const el = document.getElementById(`record-${id}`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  };

  const sessionSummary = useMemo(() => {
    if (filteredAnalyses.length < 1) return null;
    const scores = filteredAnalyses.map((rec) => Number(rec.global_score ?? rec.score ?? 0));
    const avg = scores.reduce((sum, val) => sum + val, 0) / scores.length;
    const best = Math.max(...scores);
    const worst = Math.min(...scores);
    const improvement = scores.length > 1 ? scores[scores.length - 1] - scores[0] : 0;
    return {
      count: filteredAnalyses.length,
      averageScore: avg,
      bestScore: best,
      worstScore: worst,
      improvement,
      status: improvement >= 3 ? 'Improving' : improvement <= -3 ? 'Declining' : 'Stable',
    };
  }, [filteredAnalyses]);

  const trendInsights = useMemo(() => {
    if (chartData.length < 2) return [];
    return KPI_KEYS.map((kpi) => {
      const first = chartData[0][kpi.key] ?? 0;
      const last = chartData[chartData.length - 1][kpi.key] ?? 0;
      const diff = last - first;
      return {
        label: kpi.label,
        diff: diff.toFixed(1),
        status: diff > 0 ? 'Improved' : diff < 0 ? 'Declined' : 'Stable',
        positive: diff >= 0,
      };
    });
  }, [chartData]);

  if (loading) {
    return (
      <div className="progress-table-container">
        <div className="empty-state">Loading records…</div>
      </div>
    );
  }

  return (
    <div className="progress-table-container">
      <div className="progress-table-header">
        <div>
          <h1 className="progress-table-title">Patient Records</h1>
          <p className="records-subtitle">Showing results for {patient?.name || 'selected patient'}</p>
        </div>
        <div className="records-tabs">
          <button
            className={`records-tab ${activeTab === TAB_KEYS.ANALYSIS ? 'active' : ''}`}
            onClick={() => setActiveTab(TAB_KEYS.ANALYSIS)}
          >
            Analysis Results
          </button>
          <button
            className={`records-tab ${activeTab === TAB_KEYS.PROGRESS ? 'active' : ''}`}
            onClick={() => setActiveTab(TAB_KEYS.PROGRESS)}
          >
            Progress Report
          </button>
        </div>
      </div>

      {activeTab === TAB_KEYS.ANALYSIS ? (
        <div className="analysis-tab-panel">
          {sortedAnalyses.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📭</div>
              <div className="empty-state-text">No analysis results available for this patient.</div>
              <div className="empty-state-subtext">Complete a therapy session first to view results here.</div>
            </div>
          ) : (
            <div className="table-wrapper">
              <table className="progress-table">
                <thead>
                  <tr>
                    <th className="expand-col" />
                    <th className="date-col">Date</th>
                    <th className="exercise-col">Exercise</th>
                    <th>Global</th>
                    <th>Duration</th>
                    <th>Attempts</th>
                  </tr>
                </thead>
                <tbody>
                  {tableAnalyses.map((rec) => {
                    const expanded = expandedRows.has(rec.id);
                    const detailsOpen = detailsRows.has(rec.id);
                    const globalScore = Number(rec.global_score ?? rec.score ?? 0);
                    return (
                      <React.Fragment key={rec.id}>
                        <tr id={`record-${rec.id}`} className="data-row">
                          <td className="expand-col">
                            <button className="expand-btn" onClick={() => handleToggleRow(rec.id)} aria-expanded={expanded}>
                              {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                            </button>
                          </td>
                          <td className="date-col">{formatDate(rec.createdAt)}</td>
                          <td className="exercise-col">{rec.exerciseName || rec.exercise_type || 'Unknown'}</td>
                          <td style={{ textAlign: 'center', fontWeight: '700', color: scoreColor(globalScore) }}>{globalScore.toFixed(1)}</td>
                          <td style={{ textAlign: 'center' }}>{getDurationLabel(rec)}</td>
                          <td style={{ textAlign: 'center' }}>{rec.num_attempts || 1}</td>
                        </tr>

                        {expanded && (
                          <tr className="detail-row">
                            <td colSpan="6">
                              <div className="detail-panel">
                                <div className="detail-top">
                                  <div className="score-ring-panel">
                                    <div className="score-ring-label">Global Score</div>
                                    <div className="global-score-value" style={{ color: scoreColor(globalScore) }}>{globalScore.toFixed(1)}</div>
                                  </div>

                                  <div className="kpi-cards">
                                    {KPI_KEYS.map((kpi) => {
                                      const value = Number(rec[kpi.key] ?? 0);
                                      return (
                                        <div key={kpi.key} className="kpi-card">
                                          <div className="kpi-label">{kpi.label}</div>
                                          <div className="kpi-score" style={{ color: scoreColor(value) }}>{value.toFixed(1)}</div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>

                                <div className="detail-actions">
                                  <button className="btn-secondary" onClick={() => handleToggleDetails(rec.id)}>
                                    {detailsOpen ? 'Hide Per-Attempt Details' : 'Per-Attempt Details'}
                                  </button>
                                </div>

                                {detailsOpen && (
                                  <div className="per-attempt-section">
                                    <div className="per-attempt-header">
                                      <h4>Per-Attempt Performance</h4>
                                      <span className="per-attempt-subtitle">Detailed attempt breakdown with visuals.</span>
                                    </div>

                                    {rec.per_attempt_scores?.length > 0 && (
                                      <div className="scores-grid">
                                        {rec.per_attempt_scores.map((score, idx) => (
                                          <div key={idx} className="score-card-small">
                                            <div className="score-card-label">Attempt {idx + 1}</div>
                                            <div className="score-card-value" style={{ color: scoreColor(score) }}>{Number(score).toFixed(1)}</div>
                                          </div>
                                        ))}
                                      </div>
                                    )}

                                    <div className="figures-grid">
                                      {rec.session_plot_image_b64 && (
                                        <div className="figure-card">
                                          <div className="figure-title">Global Report</div>
                                          <img
                                            src={`data:image/png;base64,${rec.session_plot_image_b64}`}
                                            alt="Global report"
                                            className="figure-image"
                                          />
                                        </div>
                                      )}

                                      {rec.plot_image_b64 && (
                                        <div className="figure-card">
                                          <div className="figure-title">3D Trajectory Comparison</div>
                                          <img
                                            src={`data:image/png;base64,${rec.plot_image_b64}`}
                                            alt="Trajectory comparison"
                                            className="figure-image"
                                          />
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                )}
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : (
        <div className="progress-tab-panel">
          <div className="filter-panel">
            <div className="filter-label">Include exercises</div>
            <div className="exercise-filters">
              {exerciseOptions.map((exercise) => (
                <button
                  key={exercise}
                  type="button"
                  className={`filter-chip ${selectedExercises.includes(exercise) ? 'active' : ''}`}
                  onClick={() => handleExerciseToggle(exercise)}
                >
                  {exercise}
                </button>
              ))}
            </div>
          </div>

          {!exerciseOptions.length ? (
            <div className="empty-state">
              <div className="empty-state-icon">📭</div>
              <div className="empty-state-text">No analysis sessions available to build a progress report.</div>
            </div>
          ) : filteredAnalyses.length < 3 ? (
            <div className="empty-state">
              <div className="empty-state-icon">📉</div>
              <div className="empty-state-text">Progress reports require at least 3 sessions.</div>
              <div className="empty-state-subtext">Complete more therapy sessions to see trends over time.</div>
            </div>
          ) : (
            <>
              <div className="summary-grid">
                <div className="summary-card">
                  <div className="summary-label">Sessions Included</div>
                  <div className="summary-value">{sessionSummary?.count}</div>
                </div>
                <div className="summary-card">
                  <div className="summary-label">Average Score</div>
                  <div className="summary-value">{sessionSummary?.averageScore.toFixed(1)}</div>
                </div>
                <div className="summary-card">
                  <div className="summary-label">Best Score</div>
                  <div className="summary-value">{sessionSummary?.bestScore.toFixed(1)}</div>
                </div>
                <div className="summary-card">
                  <div className="summary-label">Trend</div>
                  <div className="summary-value">{sessionSummary?.status}</div>
                </div>
              </div>

              <div className="trend-insights">
                {trendInsights.map((insight) => (
                  <div key={insight.label} className="insight-card">
                    <div className="insight-title">{insight.label}</div>
                    <div className={`insight-value ${insight.positive ? 'positive' : insight.status === 'Stable' ? 'neutral' : 'negative'}`}>
                      {insight.status} {Math.abs(insight.diff)}
                    </div>
                  </div>
                ))}
              </div>

              <div className="chart-card">
                <div className="chart-card-title">Global Score Trend</div>
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={chartData} margin={{ top: 20, right: 24, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#222" strokeDasharray="3 3" />
                    <XAxis dataKey="name" stroke="#cbd5e1" />
                    <YAxis domain={[0, 10]} stroke="#cbd5e1" />
                    <Tooltip contentStyle={{ backgroundColor: '#0f1419', borderColor: '#333' }} />
                    <Legend wrapperStyle={{ color: '#cbd5e1' }} />
                    <Line
                      type="monotone"
                      dataKey="global_score"
                      stroke="#00d4ff"
                      strokeWidth={3}
                      dot={(props) => renderClickableDot({ ...props, __onClick: handleSessionClick })}
                      activeDot={{ r: 7 }}
                      name="Global Score"
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div className="chart-card">
                <div className="chart-card-title">KPI Trends</div>
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart data={chartData} margin={{ top: 20, right: 24, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#222" strokeDasharray="3 3" />
                    <XAxis dataKey="name" stroke="#cbd5e1" />
                    <YAxis domain={[0, 10]} stroke="#cbd5e1" />
                    <Tooltip contentStyle={{ backgroundColor: '#0f1419', borderColor: '#333' }} />
                    <Legend wrapperStyle={{ color: '#cbd5e1' }} />
                    {KPI_KEYS.map((kpi) => (
                      <Line
                        key={kpi.key}
                        type="monotone"
                        dataKey={kpi.key}
                        stroke={kpi.color}
                        strokeWidth={2}
                        dot={(props) => renderClickableDot({ ...props, __onClick: handleSessionClick })}
                        activeDot={{ r: 7 }}
                        name={kpi.label}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
