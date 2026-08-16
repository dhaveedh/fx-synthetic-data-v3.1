import { useState, useEffect, useRef } from 'react';
import { checkHealth, generateFromPromptStreaming, getDownloadUrl } from './api';

const PRESETS = [
  { label: 'BAU production day', prompt: 'Generate 5000 trades simulating a normal business day. Standard currency mix EUR/USD 50%, GBP/USD 30%, USD/JPY 20%. 80% spot, match probability 0.3, settlement 0.5. No edge cases — clean data for ODS.' },
  { label: 'Stress test', prompt: 'Generate 3000 trades for a stress test. Heavy forwards and swaps (40% forward, 30% swap, 30% spot). High match probability 0.6. Include null rates, extreme values, and negative notional edge cases.' },
  { label: 'Regulatory audit', prompt: 'Generate 8000 clean trades for regulatory audit. 70% EUR/USD, 20% GBP/USD, 10% USD/JPY. 85% spot, high settlement ratio 0.7. No edge cases — pristine data.' },
  { label: 'Recon QA', prompt: 'Generate 2000 trades for reconciliation testing. All edge cases: null rates, duplicates, future dates, negative notionals, mismatched currencies, extreme values. Low match probability 0.15.' },
];

const STATUS_COLORS = {
  Accepted: 'pill-info', Unmatched: 'pill-warn', Matched: 'pill-pass',
  Settled: 'pill-info', Cancelled: 'pill-error', Rejected: 'pill-error',
};

const CCY_COLORS = { 'EUR/USD': '#059669', 'GBP/USD': '#2563eb', 'USD/JPY': '#ca8a04', 'AUD/USD': '#7c3aed', 'USD/CHF': '#c0533a', 'USD/CAD': '#0891b2' };
const STATUS_BAR_COLORS = { Accepted: '#7c3aed', Unmatched: '#ca8a04', Matched: '#059669', Settled: '#2563eb', Cancelled: '#dc2626', Rejected: '#c0533a' };
const TYPE_COLORS = { Spot: '#059669', Forward: '#2563eb', Swap: '#ca8a04', Option: '#7c3aed' };

function BarChart({ data, colorFn, total }) {
  const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]);
  return (
    <div>
      {sorted.map(([k, v]) => (
        <div className="bar-row" key={k}>
          <span className="bar-label">{k}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${Math.max((v / (total || 1)) * 100, 0.5)}%`, background: colorFn(k) }} />
          </div>
          <span className="bar-value">{v.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

function Pill({ severity, children }) {
  const cls = severity === 'pass' || severity === 'info' ? 'pill-pass' :
    severity === 'error' ? 'pill-error' : severity === 'warning' ? 'pill-warn' : 'pill-info';
  return <span className={`pill ${cls}`}>{children}</span>;
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [prompt, setPrompt] = useState('');
  const [maxRows, setMaxRows] = useState(5000);
  const [phase, setPhase] = useState('input'); // input | running | results | error
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [tab, setTab] = useState('overview');
  const [elapsed, setElapsed] = useState(0);
  const [progress, setProgress] = useState({ pct: 0, label: '' });
  const timerRef = useRef(null);

  // Health check on mount
  useEffect(() => {
    checkHealth()
      .then(setHealth)
      .catch(() => setHealth({ status: 'unreachable', nl_enabled: false }));
  }, []);

  // Timer during generation
  useEffect(() => {
    if (phase === 'running') {
      const t0 = Date.now();
      timerRef.current = setInterval(() => setElapsed(((Date.now() - t0) / 1000).toFixed(1)), 100);
    } else {
      clearInterval(timerRef.current);
    }
    return () => clearInterval(timerRef.current);
  }, [phase]);

  const runGenerate = async () => {
    setPhase('running');
    setError(null);
    setResult(null);
    setTab('overview');
    setElapsed(0);
    setProgress({ pct: 0, label: 'Starting...' });
    try {
      const res = await generateFromPromptStreaming({
        prompt,
        maxRows,
        onProgress: (event) => setProgress({ pct: event.pct || 0, label: event.label || '' }),
      });
      setResult(res);
      setPhase('results');
    } catch (e) {
      setError(e.message);
      setPhase('error');
    }
  };

  const resetToInput = () => {
    setPhase('input');
    setResult(null);
    setError(null);
  };

  const pipelineSteps = [
    { name: 'Agent', desc: 'Scenario reasoning', active: phase === 'running' },
    { name: 'Config', desc: 'Parameter tuning', active: result?.config != null },
    { name: 'Generator', desc: 'Deterministic build', active: result?.total_rows > 0 },
    { name: 'Validator', desc: 'Quality assurance', active: result?.validation_issues != null },
  ];

  return (
    <div className="app-shell">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="app-header">
        <div className="app-logo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
            <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
          </svg>
        </div>
        <div className="app-title-group">
          <div className="app-label">FX synthetic data</div>
          <div className="app-title">Trade generator</div>
        </div>
        {health && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: health.status === 'healthy' ? 'var(--emerald-500)' : 'var(--red-600)' }} />
            <span style={{ fontSize: 11, color: 'var(--slate-400)', fontWeight: 600 }}>
              {health.status === 'healthy' ? `API connected · ${health.claude_backend || 'no agent'}` : 'API offline'}
            </span>
          </div>
        )}
        <div className="header-actions">
          {phase === 'results' && (
            <>
              <a href={getDownloadUrl(result.download_filename)} className="btn-primary" style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                Export CSV
              </a>
              <button className="btn-secondary" onClick={resetToInput}>New run</button>
            </>
          )}
        </div>
      </div>

      {/* ── Pipeline strip ──────────────────────────────────── */}
      <div className="pipeline-strip">
        {pipelineSteps.map((step, i) => (
          <div key={i} className={`pipeline-step${step.active ? ' active' : ''}`}>
            <div className="step-name">{step.name}</div>
            <div className="step-desc">{step.desc}</div>
          </div>
        ))}
      </div>

      {/* ── Input phase ─────────────────────────────────────── */}
      {(phase === 'input' || phase === 'running') && (
        <div className="card">
          <div className="card-title">Describe your scenario</div>
          <textarea
            className="prompt-textarea"
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            disabled={phase === 'running'}
            placeholder="e.g. Generate 5000 trades for a stress test with high volatility, mostly forwards, include edge cases for null rates and extreme values..."
            rows={4}
          />
          <div className="presets">
            {PRESETS.map(p => (
              <button key={p.label} className="preset-btn" onClick={() => setPrompt(p.prompt)} disabled={phase === 'running'}>
                {p.label}
              </button>
            ))}
          </div>
          {phase === 'running' && (
            <div className="progress-bar-container">
              <div className="progress-bar-header">
                <span className="progress-bar-label">{progress.label}</span>
                <span className="progress-bar-pct">{progress.pct}%</span>
              </div>
              <div className="progress-bar-track">
                <div className="progress-bar-fill" style={{ width: `${progress.pct}%` }} />
              </div>
            </div>
          )}
          <div className="controls-row">
            <label>Max rows</label>
            <input type="range" className="range-input" min={500} max={500000} step={500} value={maxRows} onChange={e => setMaxRows(+e.target.value)} disabled={phase === 'running'} />
            <span className="range-value">{maxRows.toLocaleString()}</span>
            <div style={{ flex: 1 }} />
            <button className="btn-primary" onClick={runGenerate} disabled={!prompt.trim() || phase === 'running'}>
              {phase === 'running' ? <><span className="spinner" />{elapsed}s</> : 'Generate'}
            </button>
          </div>
        </div>
      )}

      {/* ── Error state ─────────────────────────────────────── */}
      {phase === 'error' && (
        <div className="card" style={{ borderColor: 'var(--red-100)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <Pill severity="error">Error</Pill>
            <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--red-700)' }}>Generation failed</span>
          </div>
          <p style={{ fontSize: 14, color: 'var(--slate-600)', marginBottom: 16, lineHeight: 1.6 }}>{error}</p>
          <button className="btn-secondary" onClick={resetToInput}>Try again</button>
        </div>
      )}

      {/* ── Results phase ───────────────────────────────────── */}
      {phase === 'results' && result && (
        <>
          {/* Scenario banner */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 20 }}>
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--slate-900)' }}>{result.scenario_name}</span>
            <span style={{ fontSize: 13, color: 'var(--slate-400)' }}>{result.scenario_description}</span>
            <span style={{ fontSize: 11, color: 'var(--slate-400)', marginLeft: 'auto' }}>
              {result.iterations} iteration{result.iterations > 1 ? 's' : ''} · {elapsed}s
            </span>
          </div>

          {/* Tabs */}
          <div className="tabs">
            {['overview', 'data', 'config'].map(t => (
              <button key={t} className={`tab-btn${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>
                {t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>

          {/* Overview tab */}
          {tab === 'overview' && (
            <>
              <div className="metrics-grid">
                <div className="metric-card">
                  <div className="metric-label">Total rows</div>
                  <div className="metric-value">{result.total_rows.toLocaleString()}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Unique trades</div>
                  <div className="metric-value">{result.unique_trades.toLocaleString()}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Avg versions</div>
                  <div className="metric-value">{(result.total_rows / Math.max(result.unique_trades, 1)).toFixed(1)}</div>
                </div>
                <div className="metric-card">
                  <div className="metric-label">Validation</div>
                  <div className="metric-value">{result.validation_passed ? 'Passed' : 'Failed'}</div>
                  <div className="metric-sub">{result.validation_issues.length} checks</div>
                </div>
              </div>

              {/* Issues */}
              <div style={{ marginBottom: 24 }}>
                {result.validation_issues.map((issue, i) => (
                  <div className="issue-row" key={i}>
                    <Pill severity={issue.severity}>{issue.severity === 'info' ? 'pass' : issue.severity}</Pill>
                    <span className="issue-msg">{issue.message}</span>
                  </div>
                ))}
              </div>

              {/* Edge cases */}
              {result.stats.detected_edge_cases?.length > 0 && (
                <div className="edge-banner">
                  <div className="edge-banner-title">Detected edge cases</div>
                  <div className="edge-banner-body">{result.stats.detected_edge_cases.join(' · ')}</div>
                </div>
              )}

              {/* Distribution charts */}
              <div className="charts-grid">
                <div className="bar-section">
                  <div className="bar-title">Currency distribution</div>
                  <BarChart
                    data={result.stats.actual_currency_distribution ? Object.fromEntries(Object.entries(result.stats.actual_currency_distribution).map(([k, v]) => [k, Math.round(v * result.unique_trades)])) : result.stats.status_counts || {}}
                    colorFn={k => CCY_COLORS[k] || '#666'}
                    total={result.unique_trades}
                  />
                </div>
                <div className="bar-section">
                  <div className="bar-title">Status distribution</div>
                  <BarChart data={result.stats.status_counts || {}} colorFn={k => STATUS_BAR_COLORS[k] || '#666'} total={result.total_rows} />
                </div>
              </div>
              <div className="charts-grid">
                <div className="bar-section">
                  <div className="bar-title">Trade type</div>
                  <BarChart data={result.stats.trade_type_counts || {}} colorFn={k => TYPE_COLORS[k] || '#666'} total={result.unique_trades} />
                </div>
                <div className="bar-section">
                  <div className="bar-title">Direction</div>
                  <BarChart
                    data={result.stats.actual_direction_distribution ? Object.fromEntries(Object.entries(result.stats.actual_direction_distribution).map(([k, v]) => [k, Math.round(v * result.unique_trades)])) : {}}
                    colorFn={k => k === 'Buy' ? '#059669' : '#c0533a'}
                    total={result.unique_trades}
                  />
                </div>
              </div>
            </>
          )}

          {/* Data tab */}
          {tab === 'data' && (
            <div className="data-grid-container">
              <div className="data-grid-scroll">
                <table className="data-grid">
                  <thead>
                    <tr>
                      {['trade_id', 'v', 'status', 'ccy', 'type', 'dir', 'notional', 'rate', 'party', 'cp', 'matched'].map(h => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.sample_trades.slice(0, 200).map((t, i) => (
                      <tr key={i}>
                        <td style={{ fontSize: 11 }}>{t.trade_id}</td>
                        <td>{t.version}</td>
                        <td><span className={`pill ${STATUS_COLORS[t.status] || 'pill-info'}`}>{t.status}</span></td>
                        <td>{t.currency}</td>
                        <td>{t.trade_type}</td>
                        <td>{t.direction}</td>
                        <td style={{ textAlign: 'right' }}>{typeof t.notional === 'number' ? t.notional.toLocaleString(undefined, { maximumFractionDigits: 0 }) : t.notional}</td>
                        <td style={{ textAlign: 'right' }} className={t.spot_rate === '' || t.spot_rate == null ? 'null-val' : ''}>
                          {t.spot_rate === '' || t.spot_rate == null ? 'null' : typeof t.spot_rate === 'number' ? t.spot_rate.toFixed(5) : t.spot_rate}
                        </td>
                        <td>{t.party}</td>
                        <td>{t.counterparty}</td>
                        <td style={{ color: 'var(--slate-400)', fontSize: 10 }}>{t.matched_trade_id || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="data-grid-footer">
                Showing first {Math.min(200, result.sample_trades.length)} of {result.total_rows.toLocaleString()} rows ·
                <a href={getDownloadUrl(result.download_filename)} style={{ marginLeft: 4, color: 'var(--emerald-600)', fontWeight: 600 }}>
                  Download full CSV
                </a>
              </div>
            </div>
          )}

          {/* Config tab */}
          {tab === 'config' && (
            <div className="config-viewer">
              {JSON.stringify(result.config, null, 2)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
