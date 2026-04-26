/**
 * ProgressTable.jsx - Multi-Attempt Analysis Records
 * Shows per-session scores (all out of 10) and both session plots.
 */

import React, { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import './ProgressTable.css';

const SCORE_COMPONENTS = [
  { key: 'global_score',        label: 'Global' },
  { key: 'dtw_score',           label: 'DTW' },
  { key: 'som_grade',           label: 'SoM' },
  { key: 'rom_grade',           label: 'ROM' },
  { key: 'tempo_control_grade', label: 'Tempo' },
  { key: 'hesitation_grade',    label: 'Hesitation' },
  { key: 'tremor_grade',        label: 'Tremor' },
];

export default function ProgressTable({ analysisResults = [], loading = false }) {
  const [expandedRows, setExpandedRows] = useState(new Set());

  const toggle = (id) => {
    const s = new Set(expandedRows);
    s.has(id) ? s.delete(id) : s.add(id);
    setExpandedRows(s);
  };

  const scoreColor = (s) => {
    const n = Number(s);
    if (n >= 7) return '#00e5c3';
    if (n >= 4) return '#f39c12';
    return '#ff4b6e';
  };

  const formatDate = (ts) => {
    if (!ts) return 'N/A';
    const d = ts.toDate ? ts.toDate() : new Date(ts);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '40px' }}><div className="spinner" /></div>;
  }

  return (
    <div className="progress-table-container">
      <h2>Session History</h2>

      <table className="progress-table">
        <thead>
          <tr>
            <th className="expand-col" />
            <th className="date-col">Date</th>
            <th className="exercise-col">Exercise</th>
            <th>Global</th>
            <th>DTW</th>
            <th>SoM</th>
            <th>ROM</th>
            <th>Tempo</th>
            <th>Hesitation</th>
            <th>Tremor</th>
            <th>Attempts</th>
          </tr>
        </thead>
        <tbody>
          {analysisResults.length === 0 ? (
            <tr>
              <td colSpan="11" className="empty-message">No analysis results yet</td>
            </tr>
          ) : (
            analysisResults.map((rec) => {
              const expanded = expandedRows.has(rec.id);
              return (
                <React.Fragment key={rec.id}>
                  <tr className="data-row">
                    <td className="expand-col">
                      <button className="expand-btn" onClick={() => toggle(rec.id)} aria-expanded={expanded}>
                        {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
                      </button>
                    </td>
                    <td className="date-col">{formatDate(rec.createdAt)}</td>
                    <td className="exercise-col">
                      <span className="exercise-name">{rec.exerciseName || rec.exercise_type || 'Unknown'}</span>
                    </td>
                    {SCORE_COMPONENTS.map(({ key }) => (
                      <td key={key} style={{ textAlign: 'center', fontWeight: '700', color: scoreColor(rec[key] ?? 0) }}>
                        {Number(rec[key] ?? 0).toFixed(1)}
                      </td>
                    ))}
                    <td style={{ textAlign: 'center' }}>{rec.num_attempts || 1}</td>
                  </tr>

                  {expanded && (
                    <tr className="detail-row">
                      <td colSpan="11">
                        <div className="detail-content">

                          {/* Per-attempt scores */}
                          {rec.per_attempt_scores?.length > 0 && (
                            <div style={{ marginBottom: '16px' }}>
                              <h4 style={{ marginBottom: '10px' }}>Per-Attempt Global Scores</h4>
                              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                                {rec.per_attempt_scores.map((s, idx) => (
                                  <div key={idx} style={{
                                    flex: '1 1 80px', textAlign: 'center', padding: '10px',
                                    background: 'rgba(255,255,255,0.05)', borderRadius: '8px'
                                  }}>
                                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '4px' }}>Attempt {idx + 1}</div>
                                    <div style={{ fontSize: '22px', fontWeight: '700', color: scoreColor(s) }}>{Number(s).toFixed(1)}</div>
                                    <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>/10</div>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Progression */}
                          {rec.attempt_progression && (
                            <div style={{ display: 'flex', gap: '12px', marginBottom: '16px', flexWrap: 'wrap' }}>
                              {[['Avg Score', rec.attempt_progression.avg_score],
                                ['Best', rec.attempt_progression.best_attempt],
                                ['Worst', rec.attempt_progression.worst_attempt],
                                ['Trend', rec.attempt_progression.trend]].map(([lbl, val]) => (
                                <div key={lbl} style={{
                                  padding: '8px 16px', background: 'rgba(255,255,255,0.05)',
                                  borderRadius: '8px', textAlign: 'center', minWidth: '90px'
                                }}>
                                  <div style={{ fontSize: '10px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>{lbl}</div>
                                  <div style={{ fontSize: '16px', fontWeight: '700', marginTop: '4px' }}>
                                    {typeof val === 'number' ? Number(val).toFixed(2) : val}
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Session Attempts Plot */}
                          {rec.session_attempts_plot_b64 && (
                            <div style={{ marginBottom: '16px' }}>
                              <h4 style={{ marginBottom: '8px' }}>Session Attempts — 3D Trajectory Overview</h4>
                              <img
                                src={`data:image/png;base64,${rec.session_attempts_plot_b64}`}
                                alt="Session attempts plot"
                                style={{ width: '100%', borderRadius: '8px' }}
                              />
                            </div>
                          )}

                          {/* Global Report Plot */}
                          {rec.global_report_plot_b64 && (
                            <div style={{ marginBottom: '8px' }}>
                              <h4 style={{ marginBottom: '8px' }}>Global Report — Score Breakdown</h4>
                              <img
                                src={`data:image/png;base64,${rec.global_report_plot_b64}`}
                                alt="Global report"
                                style={{ width: '100%', borderRadius: '8px' }}
                              />
                            </div>
                          )}

                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
