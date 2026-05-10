/**
 * ProgressTable.jsx - Records page with Analysis and Progress tabs.
 *
 * This component maintains the existing record table while introducing:
 * - Patient-specific header + tabs
 * - Analysis Results with expandable score ring + KPI cards
 * - Per-attempt details with ordered figures
 * - Progress Report with exercise filtering and trend charts
 */

import React, { useState, useMemo, useEffect, useCallback, useRef } from 'react';
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

/** SoM / ROM / Tempo cards show an expandable clinical breakdown */
const KPI_EXPANDABLE = new Set(['som_grade', 'rom_grade', 'tempo_control_grade']);

const axisKeys = ['X', 'Y', 'Z'];

const pickAxisTriplet = (block) => {
  if (!block || typeof block !== 'object') return null;
  const out = {};
  let any = false;
  for (const k of axisKeys) {
    const v = block[k] ?? block[k.toLowerCase()];
    if (v != null && v !== '') {
      out[k] = Number(v);
      any = true;
    } else {
      out[k] = null;
    }
  }
  return any ? out : null;
};

const averageTripletFromAttempts = (rec, field) => {
  const attempts = rec.per_attempt_metrics;
  if (!Array.isArray(attempts) || !attempts.length) return null;
  const sums = { X: 0, Y: 0, Z: 0 };
  const counts = { X: 0, Y: 0, Z: 0 };
  for (const a of attempts) {
    const block = a[field];
    if (!block || typeof block !== 'object') continue;
    for (const k of axisKeys) {
      const v = block[k];
      if (v != null && !Number.isNaN(Number(v))) {
        sums[k] += Number(v);
        counts[k] += 1;
      }
    }
  }
  const out = {};
  let any = false;
  for (const k of axisKeys) {
    out[k] = counts[k] ? Math.round((sums[k] / counts[k]) * 10000) / 10000 : null;
    if (out[k] != null) any = true;
  }
  return any ? out : null;
};

const getAxisRmseForRecord = (rec) =>
  pickAxisTriplet(rec.axis_rmse) || averageTripletFromAttempts(rec, 'axis_rmse');

const getRomAxisGradesForRecord = (rec) => {
  const raw = pickAxisTriplet(rec.rom_axis_grades) || averageTripletFromAttempts(rec, 'rom_axis_grades');
  if (!raw) return null;
  const out = {};
  for (const k of axisKeys) {
    out[k] = raw[k] != null ? Math.round(Number(raw[k]) * 10) / 10 : null;
  }
  return out;
};

const getTempoMetricsForRecord = (rec) => {
  const direct = {
    pat_velocity_rmse: rec.pat_velocity_rmse,
    ref_peak_velocity: rec.ref_peak_velocity,
    pat_peak_velocity: rec.pat_peak_velocity,
    ref_mean_velocity: rec.ref_mean_velocity,
    pat_mean_velocity: rec.pat_mean_velocity,
  };
  const hasDirect = Object.values(direct).some((v) => v != null && v !== '');
  if (hasDirect) {
    return {
      pat_velocity_rmse: direct.pat_velocity_rmse != null ? Number(direct.pat_velocity_rmse) : null,
      ref_peak_velocity: direct.ref_peak_velocity != null ? Number(direct.ref_peak_velocity) : null,
      pat_peak_velocity: direct.pat_peak_velocity != null ? Number(direct.pat_peak_velocity) : null,
      ref_mean_velocity: direct.ref_mean_velocity != null ? Number(direct.ref_mean_velocity) : null,
      pat_mean_velocity: direct.pat_mean_velocity != null ? Number(direct.pat_mean_velocity) : null,
    };
  }
  const attempts = rec.per_attempt_metrics;
  if (!Array.isArray(attempts) || !attempts.length) return null;
  const fields = ['pat_velocity_rmse', 'ref_peak_velocity', 'pat_peak_velocity', 'ref_mean_velocity', 'pat_mean_velocity'];
  const acc = {};
  for (const f of fields) acc[f] = [];
  for (const a of attempts) {
    for (const f of fields) {
      const v = a[f];
      if (v != null && v !== '') acc[f].push(Number(v));
    }
  }
  const out = {};
  let any = false;
  for (const f of fields) {
    const arr = acc[f];
    out[f] = arr.length ? Math.round((arr.reduce((s, x) => s + x, 0) / arr.length) * 10000) / 10000 : null;
    if (out[f] != null) any = true;
  }
  return any ? out : null;
};

const fmtMetric = (v, digits = 3) => {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(digits);
};

const KPI_DEFINITIONS = {
  global_score: {
    title: 'Global Score',
    short: 'Overall quality score for the session on a 0-10 scale.',
  },
  som_grade: {
    title: 'SoM (Smoothness of Movement)',
    short: 'How smooth and controlled the trajectory is over time.',
  },
  rom_grade: {
    title: 'ROM (Range of Motion)',
    short: 'How closely movement amplitude matches the target/template range.',
  },
  tempo_control_grade: {
    title: 'Tempo Control',
    short: 'How consistent and appropriate the movement timing is.',
  },
  hesitation_grade: {
    title: 'Hesitation',
    short: 'Penalizes pauses or stop-and-go behavior during movement.',
  },
  tremor_grade: {
    title: 'Tremor',
    short: 'Penalizes high-frequency jitter or oscillatory noise.',
  },
  som_axis_rmse: {
    title: 'Axis-wise Shape Error',
    short: 'Per-axis mDTW-aligned shape error (RMSE) for X, Y, and Z.',
  },
  rom_axis_grades: {
    title: 'ROM Axis Grades',
    short: 'ROM score split by X, Y, and Z axis to identify directional deficits.',
  },
  velocity_rmse: {
    title: 'Velocity RMSE',
    short: 'Timing-profile mismatch between patient and reference velocity curves.',
  },
  peak_velocity: {
    title: 'Peak Velocity',
    short: 'Maximum speed reached by reference vs patient movement profile.',
  },
  mean_velocity: {
    title: 'Mean Velocity',
    short: 'Average speed across the movement for reference vs patient.',
  },
};

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

const buildRowId = (rec) => {
  if (!rec) return 'record-unknown';
  if (rec.id) return String(rec.id);
  if (rec.sessionId) return String(rec.sessionId);

  const exercise = String(rec.exerciseName || rec.exercise_type || 'record')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-');
  const timestamp = getDateValue(rec) || 'na';
  const attempts = rec.num_attempts || rec.per_attempt_scores?.length || 1;
  const globalScore = Number(rec.global_score ?? rec.score ?? 0).toFixed(2);
  return `${exercise}-${timestamp}-${attempts}-${globalScore}`;
};

const resolveSessionIdFromDot = (payload = {}, fallback = null) => {
  if (payload.id) return String(payload.id);
  if (payload.sessionId) return String(payload.sessionId);
  if (payload.original) return buildRowId(payload.original);
  if (payload.createdAt || payload.exerciseName || payload.exercise_type) return buildRowId(payload);
  return fallback;
};

const renderClickableDot = (props, onDotClick) => {
  const payload = props.payload || {};
  const sessionId = resolveSessionIdFromDot(payload, props.id || null);
  const handleActivate = (event) => {
    event.stopPropagation();
    if (sessionId) onDotClick(sessionId);
  };

  return (
    <g
      key={`dot-${sessionId ?? props.cx}-${props.cy}`}
      style={{ cursor: 'pointer', pointerEvents: 'all' }}
      onClick={handleActivate}
      onTouchEnd={handleActivate}
      role="button"
      tabIndex={0}
      aria-label={sessionId ? `Open analysis for session ${sessionId}` : 'Open analysis session'}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleActivate(event);
        }
      }}
    >
      <circle
        cx={props.cx}
        cy={props.cy}
        r={14}
        fill="transparent"
        stroke="transparent"
        style={{ pointerEvents: 'all' }}
      />
      <circle
        cx={props.cx}
        cy={props.cy}
        r={6}
        fill={props.stroke}
        stroke="#111"
        strokeWidth={1}
        style={{ pointerEvents: 'none' }}
      />
    </g>
  );
};

export default function ProgressTable({
  patient = null,
  sessions = [],
  analyses = [],
  analysisResults: legacyAnalysisResults = [],
  loading = false,
  onSelectPatient,
  onAnalysisDeleted,
  onSessionDeleted,
  onOpenScoreMethodology,
}) {
  const [activeTab, setActiveTab] = useState(TAB_KEYS.ANALYSIS);
  const [expandedRows, setExpandedRows] = useState(new Set());
  const [detailsRows, setDetailsRows] = useState(new Set());
  const [highlightedSessionId, setHighlightedSessionId] = useState(null);
  const [selectedExercises, setSelectedExercises] = useState([]);
  const [hoverCard, setHoverCard] = useState({
    visible: false,
    x: 0,
    y: 0,
    title: '',
    short: '',
  });
  const [isHoveringInfoCard, setIsHoveringInfoCard] = useState(false);
  const hoverCloseTimeoutRef = useRef(null);
  /** rowId -> active breakdown key ('som_grade'|'rom_grade'|'tempo_control_grade'|null) */
  const [activeKpiBreakdownByRow, setActiveKpiBreakdownByRow] = useState({});

  const toggleKpiBreakdown = useCallback((rowId, kpiKey, event) => {
    if (event) event.stopPropagation();
    setActiveKpiBreakdownByRow((prev) => ({
      ...prev,
      [rowId]: prev[rowId] === kpiKey ? null : kpiKey,
    }));
  }, []);

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
      axisRmse: getAxisRmseForRecord(rec),
      romAxis: getRomAxisGradesForRecord(rec),
      tempoMetrics: getTempoMetricsForRecord(rec),
      // Use the same rowId logic for navigation
      id: buildRowId(rec),
      name: `S${index + 1}`,
      sessionLabel: rec.exerciseName || rec.exercise_type || `Session ${index + 1}`,
      createdAt: formatDate(rec.createdAt),
      global_score: Number(rec.global_score ?? rec.score ?? 0),
      som_grade: Number(rec.som_grade ?? 0),
      rom_grade: Number(rec.rom_grade ?? 0),
      tempo_control_grade: Number(rec.tempo_control_grade ?? 0),
      hesitation_grade: Number(rec.hesitation_grade ?? 0),
      tremor_grade: Number(rec.tremor_grade ?? 0),
      rmse_x: Number(getAxisRmseForRecord(rec)?.X ?? NaN),
      rmse_y: Number(getAxisRmseForRecord(rec)?.Y ?? NaN),
      rmse_z: Number(getAxisRmseForRecord(rec)?.Z ?? NaN),
      rom_grade_x: Number(getRomAxisGradesForRecord(rec)?.X ?? NaN),
      rom_grade_y: Number(getRomAxisGradesForRecord(rec)?.Y ?? NaN),
      rom_grade_z: Number(getRomAxisGradesForRecord(rec)?.Z ?? NaN),
      pat_velocity_rmse: Number(getTempoMetricsForRecord(rec)?.pat_velocity_rmse ?? NaN),
      ref_peak_velocity: Number(getTempoMetricsForRecord(rec)?.ref_peak_velocity ?? NaN),
      pat_peak_velocity: Number(getTempoMetricsForRecord(rec)?.pat_peak_velocity ?? NaN),
      ref_mean_velocity: Number(getTempoMetricsForRecord(rec)?.ref_mean_velocity ?? NaN),
      pat_mean_velocity: Number(getTempoMetricsForRecord(rec)?.pat_mean_velocity ?? NaN),
      original: rec,
    })),
    [sortedAnalyses]
  );

  const tableAnalyses = useMemo(() => [...sortedAnalyses].reverse(), [sortedAnalyses]);

  const normalizeImageSource = (raw) => {
    if (!raw) return null;
    if (typeof raw !== 'string') return null;
    const trimmed = raw.trim();
    if (trimmed.startsWith('data:image/')) return trimmed;
    return `data:image/png;base64,${trimmed}`;
  };

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

  const openKpiDetailsInNewTab = useCallback(() => {
    if (onOpenScoreMethodology) {
      onOpenScoreMethodology();
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set('tdView', 'scoring');
    window.open(url.toString(), '_blank', 'noopener,noreferrer');
  }, [onOpenScoreMethodology]);

  const showDefinitionHover = useCallback((metricKey, event) => {
    const definition = KPI_DEFINITIONS[metricKey];
    if (!definition) return;

    if (hoverCloseTimeoutRef.current) {
      window.clearTimeout(hoverCloseTimeoutRef.current);
      hoverCloseTimeoutRef.current = null;
    }
    setIsHoveringInfoCard(false);
    setHoverCard({
      visible: true,
      x: event.clientX + 14,
      y: event.clientY + 14,
      title: definition.title,
      short: definition.short,
    });
  }, []);

  const moveDefinitionHover = useCallback((event) => {
    setHoverCard((prev) => {
      if (!prev.visible || isHoveringInfoCard) return prev;
      return {
        ...prev,
        x: event.clientX + 14,
        y: event.clientY + 14,
      };
    });
  }, [isHoveringInfoCard]);

  const hideDefinitionHover = useCallback(() => {
    if (isHoveringInfoCard) return;
    if (hoverCloseTimeoutRef.current) {
      window.clearTimeout(hoverCloseTimeoutRef.current);
    }
    // Small delay allows pointer travel from source card to hover card.
    hoverCloseTimeoutRef.current = window.setTimeout(() => {
      setHoverCard((prev) => ({ ...prev, visible: false }));
      hoverCloseTimeoutRef.current = null;
    }, 180);
  }, [isHoveringInfoCard]);

  const metricHoverHandlers = useCallback((metricKey) => ({
    onMouseEnter: (event) => showDefinitionHover(metricKey, event),
    onMouseMove: moveDefinitionHover,
    onMouseLeave: hideDefinitionHover,
  }), [showDefinitionHover, moveDefinitionHover, hideDefinitionHover]);

  useEffect(() => {
    return () => {
      if (hoverCloseTimeoutRef.current) {
        window.clearTimeout(hoverCloseTimeoutRef.current);
      }
    };
  }, []);

  const handleSessionClick = useCallback((id) => {
    if (!id) return;
    setActiveTab(TAB_KEYS.ANALYSIS);
    setExpandedRows(new Set([id]));
    setDetailsRows(new Set([id]));
    setHighlightedSessionId(id);
  }, []);

  const renderChartDot = useCallback(
    (props) => renderClickableDot(props, handleSessionClick),
    [handleSessionClick]
  );

  useEffect(() => {
    if (activeTab !== TAB_KEYS.ANALYSIS || !highlightedSessionId) return;
    let attempts = 0;
    let frameId = null;

    const scrollToTarget = () => {
      const element = document.getElementById(`record-${highlightedSessionId}`);
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setHighlightedSessionId(null);
        return;
      }

      attempts += 1;
      if (attempts < 12) {
        frameId = window.requestAnimationFrame(scrollToTarget);
      }
    };

    frameId = window.requestAnimationFrame(scrollToTarget);

    return () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
    };
  }, [activeTab, highlightedSessionId]);

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
                    // Robust rowId for navigation and matching
                    const rowId = buildRowId(rec);
                    const expanded = expandedRows.has(rowId);
                    const detailsOpen = detailsRows.has(rowId);
                    const globalScore = Number(rec.global_score ?? rec.score ?? 0);
                    // Robust figure field fallback (flat and nested)
                    const plotImageRaw =
                      rec.plot_image_b64 ||
                      rec.plotImageB64 ||
                      rec.plot_image ||
                      rec.plotImage ||
                      rec.session_attempts_plot_b64 ||
                      rec.sessionAttemptsPlotB64 ||
                      (rec.figures && (
                        rec.figures.plot_image_b64 ||
                        rec.figures.plotImageB64 ||
                        rec.figures.plot_image ||
                        rec.figures.plotImage ||
                        rec.figures.session_attempts_plot_b64 ||
                        rec.figures.sessionAttemptsPlotB64
                      ));
                    const sessionPlotImageRaw =
                      rec.session_plot_image_b64 ||
                      rec.sessionPlotImageB64 ||
                      rec.session_plot_image ||
                      rec.sessionPlotImage ||
                      rec.global_report_plot_b64 ||
                      rec.globalReportPlotB64 ||
                      (rec.figures && (
                        rec.figures.session_plot_image_b64 ||
                        rec.figures.sessionPlotImageB64 ||
                        rec.figures.session_plot_image ||
                        rec.figures.sessionPlotImage ||
                        rec.figures.global_report_plot_b64 ||
                        rec.figures.globalReportPlotB64
                      ));
                    const plotImage = normalizeImageSource(plotImageRaw);
                    const sessionPlotImage = normalizeImageSource(sessionPlotImageRaw);
                    return (
                      <React.Fragment key={rowId}>
                        <tr id={`record-${rowId}`} className="data-row">
                          <td className="expand-col">
                            <button className="expand-btn" onClick={() => handleToggleRow(rowId)} aria-expanded={expanded}>
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
                                  <div
                                    className="score-ring-panel score-explainable"
                                    onMouseEnter={(event) => showDefinitionHover('global_score', event)}
                                    onMouseMove={moveDefinitionHover}
                                    onMouseLeave={hideDefinitionHover}
                                  >
                                    <div className="score-ring-label">Global Score</div>
                                    <div className="global-score-value" style={{ color: scoreColor(globalScore) }}>{globalScore.toFixed(1)}</div>
                                  </div>
                                  <div className="kpi-cards">
                                    {KPI_KEYS.map((kpi) => {
                                      const value = Number(rec[kpi.key] ?? 0);
                                      const expandable = KPI_EXPANDABLE.has(kpi.key);
                                      const open = expandable && activeKpiBreakdownByRow[rowId] === kpi.key;
                                      const axisRmse = getAxisRmseForRecord(rec);
                                      const romAxisGrades = getRomAxisGradesForRecord(rec);
                                      const tempoMetrics = getTempoMetricsForRecord(rec);

                                      return (
                                        <div
                                          key={kpi.key}
                                          className={`kpi-card score-explainable${expandable ? ' kpi-card--expandable' : ''}${open ? ' kpi-card--active' : ''}`}
                                          onMouseEnter={(event) => showDefinitionHover(kpi.key, event)}
                                          onMouseMove={moveDefinitionHover}
                                          onMouseLeave={hideDefinitionHover}
                                        >
                                          {expandable ? (
                                            <button
                                              type="button"
                                              className="kpi-card-main"
                                              onClick={(e) => toggleKpiBreakdown(rowId, kpi.key, e)}
                                              aria-expanded={!!open}
                                            >
                                              <div className="kpi-label-row">
                                                <span className="kpi-label">{kpi.label}</span>
                                                <ChevronDown size={16} className={`kpi-chevron ${open ? 'kpi-chevron--open' : ''}`} aria-hidden />
                                              </div>
                                              <div className="kpi-score" style={{ color: scoreColor(value) }}>{value.toFixed(1)}</div>
                                            </button>
                                          ) : (
                                            <div className="kpi-card-main kpi-card-main--static">
                                              <div className="kpi-label-row">
                                                <span className="kpi-label">{kpi.label}</span>
                                              </div>
                                              <div className="kpi-score" style={{ color: scoreColor(value) }}>{value.toFixed(1)}</div>
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>

                                {activeKpiBreakdownByRow[rowId] && (
                                  <div className="kpi-breakdown-panel">
                                    {activeKpiBreakdownByRow[rowId] === 'som_grade' && (
                                      <>
                                        <div className="kpi-breakdown-title">SoM Breakdown: Axis-wise shape error (m)</div>
                                        {getAxisRmseForRecord(rec) ? (
                                          <ul className="kpi-breakdown-list">
                                            {axisKeys.map((axis) => (
                                              <li key={axis} className="kpi-breakdown-interactive" {...metricHoverHandlers('som_axis_rmse')}>
                                                <span>{axis}</span>
                                                <span>{fmtMetric(getAxisRmseForRecord(rec)[axis], 3)}</span>
                                              </li>
                                            ))}
                                          </ul>
                                        ) : (
                                          <p className="kpi-breakdown-empty">No axis RMSE data for this session.</p>
                                        )}
                                      </>
                                    )}

                                    {activeKpiBreakdownByRow[rowId] === 'rom_grade' && (
                                      <>
                                        <div className="kpi-breakdown-title">ROM Breakdown: Axis grades (0-10)</div>
                                        {getRomAxisGradesForRecord(rec) ? (
                                          <ul className="kpi-breakdown-list">
                                            {axisKeys.map((axis) => (
                                              <li key={axis} className="kpi-breakdown-interactive" {...metricHoverHandlers('rom_axis_grades')}>
                                                <span>{axis}</span>
                                                <span>{getRomAxisGradesForRecord(rec)[axis] != null ? Number(getRomAxisGradesForRecord(rec)[axis]).toFixed(1) : '—'}</span>
                                              </li>
                                            ))}
                                          </ul>
                                        ) : (
                                          <p className="kpi-breakdown-empty">No per-axis ROM grades for this session.</p>
                                        )}
                                      </>
                                    )}

                                    {activeKpiBreakdownByRow[rowId] === 'tempo_control_grade' && (
                                      <>
                                        <div className="kpi-breakdown-title">Tempo Breakdown: Velocity profile metrics</div>
                                        {getTempoMetricsForRecord(rec) ? (
                                          <ul className="kpi-breakdown-list kpi-breakdown-list--stacked">
                                            <li>
                                              <span className="kpi-breakdown-interactive-inline" {...metricHoverHandlers('velocity_rmse')}>Velocity RMSE</span>
                                              <span>{fmtMetric(getTempoMetricsForRecord(rec).pat_velocity_rmse, 4)}</span>
                                            </li>
                                            <li>
                                              <span className="kpi-breakdown-interactive-inline" {...metricHoverHandlers('peak_velocity')}>Peak velocity (ref / patient)</span>
                                              <span>
                                                {fmtMetric(getTempoMetricsForRecord(rec).ref_peak_velocity, 4)} / {fmtMetric(getTempoMetricsForRecord(rec).pat_peak_velocity, 4)}
                                              </span>
                                            </li>
                                            <li>
                                              <span className="kpi-breakdown-interactive-inline" {...metricHoverHandlers('mean_velocity')}>Mean velocity (ref / patient)</span>
                                              <span>
                                                {fmtMetric(getTempoMetricsForRecord(rec).ref_mean_velocity, 4)} / {fmtMetric(getTempoMetricsForRecord(rec).pat_mean_velocity, 4)}
                                              </span>
                                            </li>
                                          </ul>
                                        ) : (
                                          <p className="kpi-breakdown-empty">No velocity metrics for this session.</p>
                                        )}
                                      </>
                                    )}
                                  </div>
                                )}

                                <div className="detail-actions">
                                  <button className="btn-secondary" onClick={() => handleToggleDetails(rowId)}>
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

                                    {(sessionPlotImage || plotImage) && (
                                      <div className="figures-grid">
                                        {sessionPlotImage && (
                                          <div className="figure-card">
                                            <div className="figure-title">Global Report</div>
                                            <img
                                              src={sessionPlotImage}
                                              alt="Global report"
                                              className="figure-image"
                                            />
                                          </div>
                                        )}
                                        {plotImage && (
                                          <div className="figure-card">
                                            <div className="figure-title">3D Trajectory Comparison</div>
                                            <img
                                              src={plotImage}
                                              alt="Trajectory comparison"
                                              className="figure-image"
                                            />
                                          </div>
                                        )}
                                      </div>
                                    )}
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
                      isAnimationActive={false}
                      dot={renderChartDot}
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
                        isAnimationActive={false}
                        dot={renderChartDot}
                        activeDot={{ r: 7 }}
                        name={kpi.label}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div className="chart-card">
                <div className="chart-card-title">Axis-wise Shape Error Trend (RMSE)</div>
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart data={chartData} margin={{ top: 20, right: 24, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#222" strokeDasharray="3 3" />
                    <XAxis dataKey="name" stroke="#cbd5e1" />
                    <YAxis stroke="#cbd5e1" />
                    <Tooltip contentStyle={{ backgroundColor: '#0f1419', borderColor: '#333' }} />
                    <Legend wrapperStyle={{ color: '#cbd5e1' }} />
                    <Line type="monotone" dataKey="rmse_x" stroke="#38bdf8" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="RMSE X" connectNulls />
                    <Line type="monotone" dataKey="rmse_y" stroke="#34d399" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="RMSE Y" connectNulls />
                    <Line type="monotone" dataKey="rmse_z" stroke="#f59e0b" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="RMSE Z" connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div className="chart-card">
                <div className="chart-card-title">ROM Axis Grade Trend</div>
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart data={chartData} margin={{ top: 20, right: 24, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#222" strokeDasharray="3 3" />
                    <XAxis dataKey="name" stroke="#cbd5e1" />
                    <YAxis domain={[0, 10]} stroke="#cbd5e1" />
                    <Tooltip contentStyle={{ backgroundColor: '#0f1419', borderColor: '#333' }} />
                    <Legend wrapperStyle={{ color: '#cbd5e1' }} />
                    <Line type="monotone" dataKey="rom_grade_x" stroke="#22d3ee" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="ROM X" connectNulls />
                    <Line type="monotone" dataKey="rom_grade_y" stroke="#22c55e" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="ROM Y" connectNulls />
                    <Line type="monotone" dataKey="rom_grade_z" stroke="#eab308" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="ROM Z" connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div className="chart-card">
                <div className="chart-card-title">Velocity Clinical Metrics Trend</div>
                <ResponsiveContainer width="100%" height={340}>
                  <LineChart data={chartData} margin={{ top: 20, right: 24, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#222" strokeDasharray="3 3" />
                    <XAxis dataKey="name" stroke="#cbd5e1" />
                    <YAxis stroke="#cbd5e1" />
                    <Tooltip contentStyle={{ backgroundColor: '#0f1419', borderColor: '#333' }} />
                    <Legend wrapperStyle={{ color: '#cbd5e1' }} />
                    <Line type="monotone" dataKey="pat_velocity_rmse" stroke="#f97316" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="Velocity RMSE" connectNulls />
                    <Line type="monotone" dataKey="ref_peak_velocity" stroke="#14b8a6" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="Peak Velocity (Ref)" connectNulls />
                    <Line type="monotone" dataKey="pat_peak_velocity" stroke="#06b6d4" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="Peak Velocity (Patient)" connectNulls />
                    <Line type="monotone" dataKey="ref_mean_velocity" stroke="#8b5cf6" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="Mean Velocity (Ref)" connectNulls />
                    <Line type="monotone" dataKey="pat_mean_velocity" stroke="#a855f7" strokeWidth={2} isAnimationActive={false} dot={renderChartDot} activeDot={{ r: 7 }} name="Mean Velocity (Patient)" connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </>
          )}
        </div>
      )}
      {hoverCard.visible && (
        <div
          className="score-hover-card"
          style={{
            left: `${hoverCard.x}px`,
            top: `${hoverCard.y}px`,
          }}
          onMouseEnter={() => {
            if (hoverCloseTimeoutRef.current) {
              window.clearTimeout(hoverCloseTimeoutRef.current);
              hoverCloseTimeoutRef.current = null;
            }
            setIsHoveringInfoCard(true);
          }}
          onMouseLeave={() => {
            setIsHoveringInfoCard(false);
            setHoverCard((prev) => ({ ...prev, visible: false }));
          }}
        >
          <div className="score-hover-title">{hoverCard.title}</div>
          <div className="score-hover-copy">{hoverCard.short}</div>
          <button className="score-hover-link" type="button" onClick={openKpiDetailsInNewTab}>
            View full details
          </button>
        </div>
      )}
    </div>
  );
}
