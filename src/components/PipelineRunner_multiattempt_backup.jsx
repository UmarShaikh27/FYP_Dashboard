/**
 * PipelineRunner.jsx - Updated for Multi-Attempt Pipeline
 * 
 * New Features:
 * - Shows real-time progress with attempt counting
 * - Displays per-attempt scores as they complete
 * - Shows attempt breakdown in results
 * - Streams weighted scoring metrics
 */

import React, { useState, useEffect } from 'react';
import axios from 'axios';
import '../PipelineRunner.css';

const API_BASE = 'http://localhost:5000';

export default function PipelineRunner({ sessionId, patientId, exerciseType = 'eight_tracing', templateFile }) {
  const [state, setState] = useState('SERVER_CHECK');
  const [message, setMessage] = useState('Checking server connection...');
  const [progress, setProgress] = useState(0);
  
  // Multi-attempt specific state
  const [currentAttempt, setCurrentAttempt] = useState(0);
  const [totalAttempts, setTotalAttempts] = useState(null);
  const [perAttemptScores, setPerAttemptScores] = useState([]);
  const [analysisResult, setAnalysisResult] = useState(null);
  
  const [recordingFile, setRecordingFile] = useState(null);
  const [error, setError] = useState(null);

  // ── State Machine ──────────────────────────────────────
  useEffect(() => {
    if (state === 'SERVER_CHECK') {
      checkServer();
    } else if (state === 'RECORDING') {
      // Recording handled by ExerciseSession component
    } else if (state === 'ANALYZING') {
      if (recordingFile) {
        analyzeRecording();
      }
    }
  }, [state, recordingFile]);

  // ── Polling for analysis progress ──────────────────────
  useEffect(() => {
    if (state === 'ANALYZING') {
      const pollInterval = setInterval(() => {
        pollPipelineStatus();
      }, 500);
      return () => clearInterval(pollInterval);
    }
  }, [state]);

  async function checkServer() {
    try {
      const response = await axios.get(`${API_BASE}/health`, { timeout: 3000 });
      if (response.status === 200) {
        setState('CONFIGURE');
        setMessage('Server connected. Ready to start recording.');
        setProgress(20);
      }
    } catch (err) {
      setError(`Server connection failed: ${err.message}`);
      setMessage('Failed to connect to analysis server');
      setProgress(0);
    }
  }

  async function startRecording(duration, exerciseName) {
    setState('RECORDING');
    setMessage('Starting motion capture...');
    setProgress(30);
    
    try {
      const response = await axios.post(`${API_BASE}/mocap/start`, {
        duration,
        grace: 6,
        exercise: exerciseName,
        trail: 'trail_1',
        arm: 'right'
      });
      
      // Poll for completion
      let isRecording = true;
      while (isRecording) {
        const statusResponse = await axios.get(`${API_BASE}/mocap/status`);
        const status = statusResponse.data;
        
        if (status.state === 'done') {
          setRecordingFile(status.output_file);
          setState('RESULTS');
          setMessage('Recording complete. Analyzing...');
          setState('ANALYZING');
          setProgress(40);
          isRecording = false;
        } else if (status.state === 'error') {
          setError(status.message);
          setState('ERROR');
          setMessage('Recording failed: ' + status.message);
          isRecording = false;
        }
        
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    } catch (err) {
      setError(err.message);
      setState('ERROR');
    }
  }

  async function analyzeRecording() {
    if (!recordingFile) {
      setError('No recording file to analyze');
      setState('ERROR');
      return;
    }

    try {
      setState('ANALYZING');
      setMessage('Starting multi-attempt analysis...');
      setProgress(45);

      const response = await axios.post(`${API_BASE}/pipeline/analyze`, {
        patient_file: recordingFile,
        template_file: templateFile,
        exercise_type: exerciseType,
        n_attempts: null, // Auto-detect
        weights_override: null
      });

      const result = response.data;

      // Update state with results
      setTotalAttempts(result.num_attempts);
      setPerAttemptScores(result.per_attempt_scores);
      setAnalysisResult(result);

      setState('RESULTS');
      setMessage(`Analysis complete! ${result.num_attempts} attempts detected.`);
      setProgress(100);

    } catch (err) {
      setError(err.response?.data?.error || err.message);
      setState('ERROR');
      setMessage(`Analysis failed: ${err.message}`);
    }
  }

  async function pollPipelineStatus() {
    try {
      const response = await axios.get(`${API_BASE}/pipeline/status`);
      const status = response.data;

      if (status.state === 'analyzing' || status.state === 'segmenting') {
        setMessage(status.message);
        setProgress(status.progress || 50);
        
        if (status.current_attempt) {
          setCurrentAttempt(status.current_attempt);
          setTotalAttempts(status.total_attempts);
        }
      } else if (status.state === 'complete') {
        // Final result is fetched via POST response
        setProgress(100);
      } else if (status.state === 'error') {
        setError(status.message);
        setState('ERROR');
      }
    } catch (err) {
      console.error('Polling failed:', err);
    }
  }

  // ── Render Progress UI ─────────────────────────────────
  const renderProgress = () => {
    if (state !== 'ANALYZING') return null;

    return (
      <div className="pipeline-progress">
        <div className="progress-bar-container">
          <div className="progress-bar" style={{ width: `${progress}%` }}>
            <span className="progress-text">{progress}%</span>
          </div>
        </div>

        {totalAttempts !== null && (
          <div className="attempt-progress">
            <p>Analyzing attempt {currentAttempt} of {totalAttempts}...</p>
            {perAttemptScores.length > 0 && (
              <div className="scores-so-far">
                <h4>Scores So Far:</h4>
                <div className="attempt-scores-list">
                  {perAttemptScores.map((score, idx) => (
                    <div key={idx} className="attempt-score-item">
                      <span className="attempt-label">Attempt {idx + 1}:</span>
                      <span className={`attempt-score ${getScoreColor(score)}`}>
                        {score.toFixed(1)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  // ── Render Results UI ──────────────────────────────────
  const renderResults = () => {
    if (!analysisResult) return null;

    const {
      global_score,
      num_attempts,
      per_attempt_scores,
      weighted_scores,
      avg_attempt_score,
      best_attempt,
      worst_attempt,
      session_summary
    } = analysisResult;

    return (
      <div className="results-container">
        <div className="global-score-card">
          <h2>Global Score</h2>
          <div className={`global-score ${getScoreColor(global_score)}`}>
            {global_score.toFixed(1)}
          </div>
          <p className="score-label">Out of 100</p>
        </div>

        <div className="session-overview">
          <h3>Session Overview</h3>
          <div className="overview-grid">
            <div className="overview-item">
              <span className="label">Total Attempts</span>
              <span className="value">{num_attempts}</span>
            </div>
            <div className="overview-item">
              <span className="label">Average Score</span>
              <span className="value">{avg_attempt_score.toFixed(1)}</span>
            </div>
            <div className="overview-item">
              <span className="label">Best Attempt</span>
              <span className="value">{best_attempt.toFixed(1)}</span>
            </div>
            <div className="overview-item">
              <span className="label">Worst Attempt</span>
              <span className="value">{worst_attempt.toFixed(1)}</span>
            </div>
          </div>
        </div>

        <div className="per-attempt-scores">
          <h3>Per-Attempt Scores</h3>
          <div className="scores-chart">
            {per_attempt_scores.map((score, idx) => (
              <div key={idx} className="score-bar-item">
                <div className="bar-label">Attempt {idx + 1}</div>
                <div className="bar-container">
                  <div
                    className={`bar ${getScoreColor(score)}`}
                    style={{ width: `${score}%` }}
                  >
                    <span className="bar-value">{score.toFixed(1)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="weighted-components">
          <h3>Weighted Score Components</h3>
          <div className="components-grid">
            {Object.entries(weighted_scores || {}).map(([key, value]) => (
              <div key={key} className="component-item">
                <span className="component-label">
                  {key.toUpperCase().replace(/_/g, ' ')}
                </span>
                <span className={`component-score ${getScoreColor(value * 10)}`}>
                  {value.toFixed(1)}/10
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="session-summary">
          <h3>Session Summary</h3>
          <p className="summary-text">{session_summary}</p>
        </div>
      </div>
    );
  };

  // ── Helper Functions ──────────────────────────────────
  function getScoreColor(score) {
    if (score >= 80) return 'score-good';
    if (score >= 50) return 'score-moderate';
    return 'score-poor';
  }

  // ── Main Render ────────────────────────────────────────
  return (
    <div className="pipeline-runner">
      <div className="pipeline-header">
        <h1>Exercise Analysis Pipeline</h1>
        <p className="pipeline-state">{state}</p>
      </div>

      <div className="pipeline-content">
        {error && (
          <div className="error-alert">
            <p><strong>Error:</strong> {error}</p>
          </div>
        )}

        <div className="message-box">
          <p>{message}</p>
        </div>

        {state === 'RECORDING' && (
          <button
            className="btn btn-secondary"
            onClick={() => setState('RESULTS')}
          >
            End Recording
          </button>
        )}

        {renderProgress()}
        {renderResults()}

        {state === 'RESULTS' && (
          <div className="action-buttons">
            <button
              className="btn btn-primary"
              onClick={() => window.location.href = '/dashboard'}
            >
              Back to Dashboard
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => window.location.reload()}
            >
              New Session
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
