// components/PatientProgress.jsx
// Progress tracking panel shown inside the Records tab.
// Requires at least 3 analysis sessions to render charts.
//
// Axis → movement mapping (RealSense coordinate system, right arm):
//   X axis = horizontal = Abduction / Adduction
//   Y axis = vertical   = Flexion / Extension
//   Z axis = depth      = Internal / External Rotation

import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ReferenceLine, ResponsiveContainer,
  BarChart, Bar, Cell,
} from "recharts";

const PASS_THRESHOLD = 70;
const GRADE_PASS     = 7;

const C = {
  score:     "#00e5c3",
  rom:       "#0090ff",
  shape:     "#f59e0b",
  smooth:    "#a78bfa",
  abduction: "#34d399",
  flexion:   "#60a5fa",
  rotation:  "#f87171",
  pass:      "#00e5c3",
  fail:      "#ff4b6e",
  grid:      "#232a3a",
  muted:     "#6b7a96",
};

const tooltipStyle = {
  backgroundColor: "#1a2030",
  border: "1px solid #232a3a",
  borderRadius: 8,
  color: "#e8edf5",
  fontSize: 12,
};

function calcTrend(values) {
  if (values.length < 2) return "stable";
  const recent = values.slice(-3);
  const diff   = recent[recent.length - 1] - recent[0];
  if (diff > 3)  return "improving";
  if (diff < -3) return "declining";
  return "stable";
}
function trendColor(t) {
  if (t === "improving") return C.score;
  if (t === "declining") return C.fail;
  return C.muted;
}
function trendLabel(t) {
  if (t === "improving") return "Improving";
  if (t === "declining") return "Declining";
  return "Stable";
}

function StatBox({ label, value, color, sub }) {
  return (
    <div className="prog-stat-box">
      <span className="prog-stat-value" style={{ color }}>{value}</span>
      <span className="prog-stat-label">{label}</span>
      {sub && <span className="prog-stat-sub">{sub}</span>}
    </div>
  );
}

function SectionHeading({ title, subtitle }) {
  return (
    <div className="prog-section-heading">
      <h3>{title}</h3>
      {subtitle && <p>{subtitle}</p>}
    </div>
  );
}

// ── Custom active dot — shows pointer cursor and highlights on hover ──────────
function ClickableDot({ cx, cy, fill, onClick }) {
  if (cx == null || cy == null) return null;
  return (
    <circle
      cx={cx} cy={cy} r={6}
      fill={fill} stroke="#0a0d12" strokeWidth={2}
      style={{ cursor: "pointer" }}
      onClick={onClick}
    />
  );
}

// ── Main component ────────────────────────────────────────────────────────────
// onSelectSession(id) — called when a chart dot is clicked.
//   The parent switches to the analyses tab and expands that session card.
export default function PatientProgress({ analyses, patientName, onSelectSession }) {
  const sorted = [...analyses]
    .filter((a) => a.score != null)
    .reverse(); // chronological order for charts

  if (sorted.length < 3) {
    return (
      <div className="prog-insufficient">
        <p>
          <strong>{sorted.length} session{sorted.length !== 1 ? "s" : ""} recorded.</strong>
          {" "}At least 3 sessions are needed to show progress trends.
        </p>
      </div>
    );
  }

  // Build chart data — each entry keeps the Firestore id so clicks can reference it
  const chartData = sorted.map((a, i) => ({
    id:        a.id,
    session:   `S${i + 1}`,
    date:      a.createdAt?.toDate?.().toLocaleDateString() ?? `Session ${i + 1}`,
    score:     a.score,
    rom:       a.avg_rom_grade ? Math.round(a.avg_rom_grade) : null,
    shape:     a.shape_grade   ?? null,
    smooth:    a.sparc_grades?.total ?? null,
    abduction: a.rom_axis_grades?.[0] ?? null,
    flexion:   a.rom_axis_grades?.[1] ?? null,
    rotation:  a.rom_axis_grades?.[2] ?? null,
  }));

  // Handler called by Recharts onClick — activePayload carries the clicked entry
  const handleChartClick = (chartState) => {
    if (!chartState?.activePayload?.length) return;
    const id = chartState.activePayload[0]?.payload?.id;
    if (id && onSelectSession) onSelectSession(id);
  };

  // Bar chart click — receives the bar entry directly
  const handleBarClick = (entry) => {
    // Bar chart uses axisBarData which has no id — navigate to latest session
    const latest = sorted[sorted.length - 1];
    if (latest && onSelectSession) onSelectSession(latest.id);
  };

  // Summary stats
  const scores      = sorted.map((a) => a.score).filter(Boolean);
  const latestScore = scores[scores.length - 1];
  const bestScore   = Math.max(...scores);
  const scoreTrend  = calcTrend(scores);
  const romTrend    = calcTrend(sorted.map((a) => a.avg_rom_grade).filter(Boolean));
  const shapeTrend  = calcTrend(sorted.map((a) => a.shape_grade).filter(Boolean));
  const smoothGrades = sorted.map((a) => a.sparc_grades?.total).filter((v) => v != null);
  const smoothTrend  = calcTrend(smoothGrades);

  const recentSlice = sorted.slice(-Math.min(3, sorted.length));
  const avgRecent = (fn) => {
    const vals = recentSlice.map(fn).filter((v) => v != null);
    return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
  };
  const metricAvgs = {
    "ROM":        avgRecent((a) => a.avg_rom_grade),
    "Shape":      avgRecent((a) => a.shape_grade),
    "Smoothness": avgRecent((a) => a.sparc_grades?.total),
  };
  const worstMetric = Object.entries(metricAvgs)
    .filter(([, v]) => v != null)
    .sort(([, a], [, b]) => a - b)[0]?.[0] ?? "—";

  const latestAnalysis = sorted[sorted.length - 1];
  const axisBarData = [
    { name: "Abduction (X)", grade: latestAnalysis.rom_axis_grades?.[0] ?? 0, color: C.abduction },
    { name: "Flexion (Y)",   grade: latestAnalysis.rom_axis_grades?.[1] ?? 0, color: C.flexion   },
    { name: "Rotation (Z)",  grade: latestAnalysis.rom_axis_grades?.[2] ?? 0, color: C.rotation  },
  ];

  // Hint shown below charts
  const clickHint = onSelectSession
    ? <p className="prog-click-hint">Click any data point to open that session in the Analysis Results tab.</p>
    : null;

  return (
    <div className="prog-container">
      <div className="prog-header">
        <h2>Progress Overview</h2>
        <p className="prog-subheading">{patientName} — {sorted.length} sessions analysed</p>
      </div>

      {/* Section 4: Summary stats */}
      <div className="prog-stats-row">
        <StatBox label="Latest Score"    value={`${latestScore}/100`}    color={latestScore >= PASS_THRESHOLD ? C.score : C.fail} sub={`Trend: ${trendLabel(scoreTrend)}`} />
        <StatBox label="Best Score"      value={`${bestScore}/100`}      color={C.score}            sub={`${sorted.length} sessions`} />
        <StatBox label="Overall Trend"   value={trendLabel(scoreTrend)}  color={trendColor(scoreTrend)} sub="Based on last 3 sessions" />
        <StatBox label="Needs Attention" value={worstMetric}             color={C.fail}             sub="Lowest recent average" />
      </div>

      {/* Section 1: Score timeline */}
      <div className="prog-chart-card">
        <SectionHeading
          title="Overall Score Timeline"
          subtitle="DTW similarity score per session. Pass threshold is 70/100."
        />
        {clickHint}
        <ResponsiveContainer width="100%" height={220}>
          <LineChart
            data={chartData}
            margin={{ top: 8, right: 20, bottom: 8, left: 0 }}
            onClick={handleChartClick}
            style={{ cursor: "pointer" }}
          >
            <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
            <XAxis dataKey="session" tick={{ fill: C.muted, fontSize: 11 }} />
            <YAxis domain={[0, 100]} tick={{ fill: C.muted, fontSize: 11 }} />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(v) => [`${v}/100`, "Score"]}
              labelFormatter={(l, payload) => `${payload?.[0]?.payload?.date ?? l} — click to open`}
            />
            <ReferenceLine
              y={PASS_THRESHOLD}
              stroke={C.pass} strokeDasharray="5 5"
              label={{ value: "Pass (70)", fill: C.pass, fontSize: 11, position: "insideTopRight" }}
            />
            <Line
              type="monotone" dataKey="score"
              stroke={C.score} strokeWidth={2.5}
              dot={({ key, ...props }) => (
                <ClickableDot
                  key={key}
                  {...props}
                  fill={C.score}
                  onClick={() => onSelectSession?.(props.payload?.id)}
                />
              )}
              activeDot={{ r: 7, fill: C.score, style: { cursor: "pointer" } }}
              name="Score"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Section 2: Clinical grade breakdown */}
      <div className="prog-chart-card">
        <SectionHeading
          title="Clinical Grade Breakdown"
          subtitle="ROM, Shape accuracy, and Movement smoothness grades per session (out of 10)."
        />
        <div className="prog-trend-row">
          <span style={{ color: C.rom }}>ROM: <b style={{ color: trendColor(romTrend) }}>{trendLabel(romTrend)}</b></span>
          <span style={{ color: C.shape }}>Shape: <b style={{ color: trendColor(shapeTrend) }}>{trendLabel(shapeTrend)}</b></span>
          {smoothGrades.length > 0 && (
            <span style={{ color: C.smooth }}>Smoothness: <b style={{ color: trendColor(smoothTrend) }}>{trendLabel(smoothTrend)}</b></span>
          )}
        </div>
        {clickHint}
        <ResponsiveContainer width="100%" height={220}>
          <LineChart
            data={chartData}
            margin={{ top: 8, right: 20, bottom: 8, left: 0 }}
            onClick={handleChartClick}
            style={{ cursor: "pointer" }}
          >
            <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
            <XAxis dataKey="session" tick={{ fill: C.muted, fontSize: 11 }} />
            <YAxis domain={[0, 10]} tick={{ fill: C.muted, fontSize: 11 }} />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(v, name) => [`${v}/10`, name]}
              labelFormatter={(l, payload) => `${payload?.[0]?.payload?.date ?? l} — click to open`}
            />
            <ReferenceLine y={GRADE_PASS} stroke={C.muted} strokeDasharray="4 4"
              label={{ value: "Pass (7)", fill: C.muted, fontSize: 10, position: "insideTopRight" }}
            />
            <Line type="monotone" dataKey="rom"
              stroke={C.rom} strokeWidth={2}
              dot={({ key, ...props }) => <ClickableDot key={key} {...props} fill={C.rom} onClick={() => onSelectSession?.(props.payload?.id)} />}
              activeDot={{ r: 6, style: { cursor: "pointer" } }} name="ROM"
            />
            <Line type="monotone" dataKey="shape"
              stroke={C.shape} strokeWidth={2}
              dot={({ key, ...props }) => <ClickableDot key={key} {...props} fill={C.shape} onClick={() => onSelectSession?.(props.payload?.id)} />}
              activeDot={{ r: 6, style: { cursor: "pointer" } }} name="Shape"
            />
            {smoothGrades.length > 0 && (
              <Line type="monotone" dataKey="smooth"
                stroke={C.smooth} strokeWidth={2}
                dot={({ key, ...props }) => <ClickableDot key={key} {...props} fill={C.smooth} onClick={() => onSelectSession?.(props.payload?.id)} />}
                activeDot={{ r: 6, style: { cursor: "pointer" } }} name="Smoothness"
              />
            )}
            <Legend wrapperStyle={{ fontSize: 12, color: C.muted, paddingTop: 8 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Section 3: Axis ROM trend + latest session bar */}
      <div className="prog-chart-grid">
        <div className="prog-chart-card">
          <SectionHeading
            title="Movement Plane Grades Over Time"
            subtitle="Abduction = side raise (X), Flexion = forward raise (Y), Rotation = depth (Z)."
          />
          {clickHint}
          <ResponsiveContainer width="100%" height={220}>
            <LineChart
              data={chartData}
              margin={{ top: 8, right: 20, bottom: 8, left: 0 }}
              onClick={handleChartClick}
              style={{ cursor: "pointer" }}
            >
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
              <XAxis dataKey="session" tick={{ fill: C.muted, fontSize: 11 }} />
              <YAxis domain={[0, 10]} tick={{ fill: C.muted, fontSize: 11 }} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v, name) => [`${v}/10`, name]}
                labelFormatter={(l, payload) => `${payload?.[0]?.payload?.date ?? l} — click to open`}
              />
              <ReferenceLine y={GRADE_PASS} stroke={C.muted} strokeDasharray="4 4" />
              <Line type="monotone" dataKey="abduction"
                stroke={C.abduction} strokeWidth={2}
                dot={({ key, ...props }) => <ClickableDot key={key} {...props} fill={C.abduction} onClick={() => onSelectSession?.(props.payload?.id)} />}
                activeDot={{ r: 6, style: { cursor: "pointer" } }} name="Abduction (X)"
              />
              <Line type="monotone" dataKey="flexion"
                stroke={C.flexion} strokeWidth={2}
                dot={({ key, ...props }) => <ClickableDot key={key} {...props} fill={C.flexion} onClick={() => onSelectSession?.(props.payload?.id)} />}
                activeDot={{ r: 6, style: { cursor: "pointer" } }} name="Flexion (Y)"
              />
              <Line type="monotone" dataKey="rotation"
                stroke={C.rotation} strokeWidth={2}
                dot={({ key, ...props }) => <ClickableDot key={key} {...props} fill={C.rotation} onClick={() => onSelectSession?.(props.payload?.id)} />}
                activeDot={{ r: 6, style: { cursor: "pointer" } }} name="Rotation (Z)"
              />
              <Legend wrapperStyle={{ fontSize: 12, color: C.muted, paddingTop: 8 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="prog-chart-card">
          <SectionHeading
            title="Latest Session — Movement Plane Grades"
            subtitle="Click any bar to open the latest session record."
          />
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              data={axisBarData}
              margin={{ top: 8, right: 20, bottom: 8, left: 0 }}
              onClick={handleBarClick}
              style={{ cursor: "pointer" }}
            >
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" />
              <XAxis dataKey="name" tick={{ fill: C.muted, fontSize: 11 }} />
              <YAxis domain={[0, 10]} tick={{ fill: C.muted, fontSize: 11 }} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v) => [`${v}/10`, "Grade"]}
                labelFormatter={(l) => `${l} — click to open latest session`}
              />
              <ReferenceLine y={GRADE_PASS} stroke={C.muted} strokeDasharray="4 4"
                label={{ value: "Pass", fill: C.muted, fontSize: 10, position: "insideTopRight" }}
              />
              <Bar dataKey="grade" radius={[4, 4, 0, 0]}>
                {axisBarData.map((entry, i) => (
                  <Cell key={i} fill={entry.grade >= GRADE_PASS ? C.score : C.fail} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
