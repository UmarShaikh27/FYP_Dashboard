/**
 * PatientProgress.jsx - Updated for Multi-Attempt Trends
 * 
 * Features:
 * - Score trend chart showing per-attempt progression
 * - Weighted components comparison
 * - Session-level aggregate metrics
 * - ROM tracking with attempt breakdown
 * - Velocity profile visualization
 */

import React, { useMemo } from 'react';
import {
  LineChart, Line, BarChart, Bar, ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  Cell
} from 'recharts';
import './PatientProgress.css';

export default function PatientProgress({ analysisResults = [] }) {
  
  // ── Sort results by date ────────────────────────────────────
  const sortedResults = useMemo(() => {
    return [...analysisResults].sort((a, b) => {
      const dateA = a.createdAt?.toDate ? a.createdAt.toDate() : new Date(a.createdAt);
      const dateB = b.createdAt?.toDate ? b.createdAt.toDate() : new Date(b.createdAt);
      return dateA - dateB;
    });
  }, [analysisResults]);

  // ── Score Progression Chart Data ────────────────────────────
  const scoreProgressionData = useMemo(() => {
    return sortedResults.flatMap((result, sessionIdx) => {
      const sessionDate = result.createdAt?.toDate ? result.createdAt.toDate() : new Date(result.createdAt);
      const dateStr = sessionDate.toLocaleDateString();
      
      if (result.num_attempts && result.num_attempts > 1) {
        // Multi-attempt session
        return (result.per_attempt_scores || []).map((score, attemptIdx) => ({
          session: sessionIdx,
          sessionDate: dateStr,
          attemptNumber: attemptIdx + 1,
          totalAttempts: result.num_attempts,
          score: score,
          global: false,
          exerciseType: result.exercise_type || 'unknown'
        }));
      } else {
        // Single attempt (legacy)
        return [{
          session: sessionIdx,
          sessionDate: dateStr,
          attemptNumber: 1,
          totalAttempts: 1,
          score: result.global_score || result.score,
          global: true,
          exerciseType: result.exercise_type || 'unknown'
        }];
      }
    });
  }, [sortedResults]);

  // ── Session-Level Averages Data ─────────────────────────────
  const sessionAveragesData = useMemo(() => {
    return sortedResults.map((result, idx) => {
      const sessionDate = result.createdAt?.toDate ? result.createdAt.toDate() : new Date(result.createdAt);
      const dateStr = sessionDate.toLocaleDateString();
      
      const globalScore = result.global_score || result.score || 0;
      const avgAttemptScore = result.avg_attempt_score || globalScore;
      const numAttempts = result.num_attempts || 1;
      
      return {
        session: `Session ${idx + 1}`,
        date: dateStr,
        globalScore: globalScore,
        avgAttemptScore: avgAttemptScore,
        numAttempts: numAttempts,
        bestAttempt: result.best_attempt || globalScore,
        worstAttempt: result.worst_attempt || globalScore
      };
    });
  }, [sortedResults]);

  // ── Weighted Components Data ────────────────────────────────
  const weightedComponentsData = useMemo(() => {
    return sortedResults
      .filter(r => r.weighted_scores)
      .map((result, idx) => {
        const sessionDate = result.createdAt?.toDate ? result.createdAt.toDate() : new Date(result.createdAt);
        const dateStr = sessionDate.toLocaleDateString();
        
        return {
          session: `Session ${idx + 1}`,
          date: dateStr,
          som: result.weighted_scores.som || 0,
          rom: result.weighted_scores.rom || 0,
          tremor: result.weighted_scores.tremor || 0,
          hesitation: result.weighted_scores.hesitation || 0,
          tempo_control: result.weighted_scores.tempo_control || 0,
          velocity_profile: result.weighted_scores.velocity_profile || 0
        };
      });
  }, [sortedResults]);

  // ── ROM Grades Progression ──────────────────────────────────
  const romProgressionData = useMemo(() => {
    return sortedResults
      .filter(r => r.per_attempt_metrics)
      .flatMap((result, sessionIdx) => {
        const sessionDate = result.createdAt?.toDate ? result.createdAt.toDate() : new Date(result.createdAt);
        const dateStr = sessionDate.toLocaleDateString();
        
        return (result.per_attempt_metrics || []).map((attempt, attemptIdx) => ({
          session: sessionIdx,
          sessionDate: dateStr,
          attemptNumber: attemptIdx + 1,
          romGrade: attempt.rom_grade || 0,
          romRatio: (attempt.rom_ratio || 0) * 100
        }));
      });
  }, [sortedResults]);

  // ── Peak Velocity Trend ─────────────────────────────────────
  const velocityTrendData = useMemo(() => {
    return sortedResults
      .filter(r => r.per_attempt_metrics)
      .flatMap((result, sessionIdx) => {
        const sessionDate = result.createdAt?.toDate ? result.createdAt.toDate() : new Date(result.createdAt);
        const dateStr = sessionDate.toLocaleDateString();
        
        return (result.per_attempt_metrics || []).map((attempt, attemptIdx) => ({
          session: sessionIdx,
          sessionDate: dateStr,
          attemptNumber: attemptIdx + 1,
          peakVelocity: attempt.peak_velocity || 0,
          velocityRmse: attempt.velocity_rmse || 0
        }));
      });
  }, [sortedResults]);

  // ── Helper: Get color based on score ────────────────────────
  const getScoreColor = (score) => {
    if (score >= 80) return '#00e5c3';      // Cyan (good)
    if (score >= 50) return '#0090ff';      // Blue (moderate)
    return '#ff4b6e';                       // Red (poor)
  };

  // ── Helper: Custom tooltip for charts ───────────────────────
  const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
      return (
        <div className="custom-tooltip">
          {payload.map((entry, idx) => (
            <p key={idx} style={{ color: entry.color }}>
              {entry.name}: {entry.value.toFixed(2)}
            </p>
          ))}
        </div>
      );
    }
    return null;
  };

  // ── Stats Summary ───────────────────────────────────────────
  const stats = useMemo(() => {
    if (sortedResults.length === 0) {
      return { avg: 0, max: 0, min: 0, latest: 0, totalSessions: 0, totalAttempts: 0 };
    }

    const allScores = sortedResults.map(r => r.global_score || r.score);
    const totalAttempts = sortedResults.reduce((sum, r) => sum + (r.num_attempts || 1), 0);

    return {
      avg: (allScores.reduce((a, b) => a + b, 0) / allScores.length).toFixed(1),
      max: Math.max(...allScores).toFixed(1),
      min: Math.min(...allScores).toFixed(1),
      latest: (allScores[allScores.length - 1]).toFixed(1),
      totalSessions: sortedResults.length,
      totalAttempts: totalAttempts
    };
  }, [sortedResults]);

  // ── Render ──────────────────────────────────────────────────
  if (sortedResults.length === 0) {
    return (
      <div className="patient-progress">
        <div className="empty-state">
          <p>No progress data available yet. Complete some exercise sessions to see trends.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="patient-progress">
      {/* ── Stats Summary ────────────────────────────────────── */}
      <div className="stats-summary">
        <h2>Progress Overview</h2>
        <div className="stats-grid">
          <div className="stat-card">
            <span className="stat-label">Latest Score</span>
            <span className={`stat-value score-${stats.latest >= 80 ? 'good' : stats.latest >= 50 ? 'moderate' : 'poor'}`}>
              {stats.latest}
            </span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Average Score</span>
            <span className="stat-value">{stats.avg}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Best Score</span>
            <span className="stat-value score-good">{stats.max}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Sessions</span>
            <span className="stat-value">{stats.totalSessions}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Total Attempts</span>
            <span className="stat-value">{stats.totalAttempts}</span>
          </div>
        </div>
      </div>

      {/* ── Score Progression Chart ────────────────────────────── */}
      <div className="chart-container">
        <h3>Score Progression (Per-Attempt)</h3>
        <ResponsiveContainer width="100%" height={300}>
          <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#232a3a" />
            <XAxis
              type="number"
              dataKey="session"
              name="Session"
              stroke="#6b7a96"
            />
            <YAxis
              type="number"
              dataKey="score"
              name="Score"
              domain={[0, 100]}
              stroke="#6b7a96"
            />
            <Tooltip cursor={{ strokeDasharray: '3 3' }} content={<CustomTooltip />} />
            <Scatter
              name="Attempt Scores"
              data={scoreProgressionData}
              fill="#00e5c3"
              shape="circle"
            >
              {scoreProgressionData.map((entry, idx) => (
                <Cell key={idx} fill={getScoreColor(entry.score)} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      {/* ── Session Averages Chart ──────────────────────────────── */}
      <div className="chart-container">
        <h3>Session-Level Scores</h3>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={sessionAveragesData} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#232a3a" />
            <XAxis dataKey="session" stroke="#6b7a96" />
            <YAxis domain={[0, 100]} stroke="#6b7a96" />
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ color: '#6b7a96' }} />
            <Bar dataKey="globalScore" fill="#00e5c3" name="Global Score" />
            <Bar dataKey="avgAttemptScore" fill="#0090ff" name="Avg Attempt Score" />
            <Bar dataKey="bestAttempt" fill="#00e5c3" name="Best Attempt" opacity={0.5} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* ── Weighted Components Radar Chart ──────────────────────── */}
      {weightedComponentsData.length > 0 && (
        <div className="chart-container">
          <h3>Weighted Score Components Trend</h3>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={weightedComponentsData} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#232a3a" />
              <XAxis dataKey="session" stroke="#6b7a96" />
              <YAxis domain={[0, 10]} stroke="#6b7a96" />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ color: '#6b7a96' }} />
              <Line type="monotone" dataKey="som" stroke="#00e5c3" name="SOM (Shape)" />
              <Line type="monotone" dataKey="rom" stroke="#0090ff" name="ROM (Range)" />
              <Line type="monotone" dataKey="tremor" stroke="#ff4b6e" name="Tremor" />
              <Line type="monotone" dataKey="hesitation" stroke="#ffa500" name="Hesitation" />
              <Line type="monotone" dataKey="tempo_control" stroke="#00ff88" name="Tempo Control" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── ROM Progression Chart ──────────────────────────────── */}
      {romProgressionData.length > 0 && (
        <div className="chart-container">
          <h3>ROM (Range of Motion) Progression</h3>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={romProgressionData} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#232a3a" />
              <XAxis dataKey="session" stroke="#6b7a96" />
              <YAxis yAxisId="left" domain={[0, 10]} stroke="#6b7a96" />
              <YAxis yAxisId="right" domain={[0, 100]} orientation="right" stroke="#6b7a96" />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ color: '#6b7a96' }} />
              <Line yAxisId="left" type="monotone" dataKey="romGrade" stroke="#00e5c3" name="ROM Grade" />
              <Line yAxisId="right" type="monotone" dataKey="romRatio" stroke="#0090ff" name="ROM Ratio (%)" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── Velocity Trend Chart ────────────────────────────────── */}
      {velocityTrendData.length > 0 && (
        <div className="chart-container">
          <h3>Peak Velocity Trend</h3>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={velocityTrendData} margin={{ top: 20, right: 30, left: 0, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#232a3a" />
              <XAxis dataKey="session" stroke="#6b7a96" />
              <YAxis yAxisId="left" stroke="#6b7a96" />
              <YAxis yAxisId="right" orientation="right" stroke="#6b7a96" />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ color: '#6b7a96' }} />
              <Line yAxisId="left" type="monotone" dataKey="peakVelocity" stroke="#00e5c3" name="Peak Velocity (m/s)" />
              <Line yAxisId="right" type="monotone" dataKey="velocityRmse" stroke="#0090ff" name="Velocity RMSE" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
