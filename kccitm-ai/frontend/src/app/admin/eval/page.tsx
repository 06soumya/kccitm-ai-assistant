'use client';
import { useEffect, useState, useCallback } from 'react';
import {
  getEvalQueries, startEvalRun, listEvalRuns, getEvalRun, getLatestEvalRun,
} from '@/lib/adminApi';
import MetricCard from '@/components/admin/MetricCard';
import { Play, CheckCircle2, XCircle, AlertTriangle, Loader2, RefreshCw } from 'lucide-react';

interface EvalQuery {
  id: string;
  category: string;
  query: string;
  expected_route: string;
  accept_routes?: string[];
  ground_truth_sql?: string;
  expected_substring?: string;
}

interface EvalResult {
  query_id: string;
  category: string;
  query_text: string;
  expected_route: string;
  actual_route: string;
  route_match: number;
  expected_value: string | null;
  actual_value: string | null;
  value_match: number | null;
  response: string;
  error: string | null;
  duration_ms: number;
}

interface EvalRun {
  id: string;
  started_at: string;
  finished_at: string | null;
  total: number;
  completed: number;
  passed: number;
  failed: number;
  errored: number;
  status: string;
  results?: EvalResult[];
}

const CAT_COLORS: Record<string, string> = {
  aggregate: 'bg-blue-50 text-blue-700',
  list: 'bg-purple-50 text-purple-700',
  comparison: 'bg-indigo-50 text-indigo-700',
  student_lookup: 'bg-emerald-50 text-emerald-700',
  rag: 'bg-amber-50 text-amber-700',
  meta: 'bg-gray-100 text-gray-700',
  clarification: 'bg-rose-50 text-rose-700',
  concept: 'bg-sky-50 text-sky-700',
};

export default function EvalPage() {
  const [queries, setQueries] = useState<EvalQuery[]>([]);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [currentRun, setCurrentRun] = useState<EvalRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [activeTab, setActiveTab] = useState<'queries' | 'runs'>('queries');

  const loadData = useCallback(async () => {
    try {
      const [qs, rs] = await Promise.all([getEvalQueries(), listEvalRuns(20)]);
      setQueries(qs.queries || []);
      setRuns(rs.runs || []);
      // If there's a running one, show it as current
      const running = (rs.runs || []).find((r: EvalRun) => r.status === 'running');
      if (running) {
        const detail = await getEvalRun(running.id);
        setCurrentRun(detail);
      } else if ((rs.runs || []).length > 0) {
        const latest = await getLatestEvalRun().catch(() => null);
        if (latest) setCurrentRun(latest);
      }
    } catch {
      // Non-fatal: page will just show empty states
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // Poll while a run is active
  useEffect(() => {
    if (!currentRun || currentRun.status !== 'running') return;
    const t = setInterval(async () => {
      try {
        const updated = await getEvalRun(currentRun.id);
        setCurrentRun(updated);
        if (updated.status !== 'running') {
          // refresh the list
          const rs = await listEvalRuns(20);
          setRuns(rs.runs || []);
        }
      } catch {}
    }, 3000);
    return () => clearInterval(t);
  }, [currentRun?.id, currentRun?.status]);

  const handleStartRun = async () => {
    setStarting(true);
    try {
      const r = await startEvalRun();
      // Optimistically show as running
      setCurrentRun({
        id: r.run_id, started_at: new Date().toISOString(), finished_at: null,
        total: r.total, completed: 0, passed: 0, failed: 0, errored: 0,
        status: 'running',
      });
      // Refresh runs list shortly after
      setTimeout(() => loadData(), 1000);
    } catch (err: any) {
      alert('Failed to start eval run: ' + (err.message || String(err)));
    } finally {
      setStarting(false);
    }
  };

  const handleLoadRun = async (id: string) => {
    const detail = await getEvalRun(id);
    setCurrentRun(detail);
  };

  if (loading) {
    return <div className="text-sm text-gray-500">Loading eval set…</div>;
  }

  const passRate = currentRun && currentRun.completed > 0
    ? (currentRun.passed / currentRun.completed * 100).toFixed(1)
    : null;

  return (
    <div className="max-w-6xl">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Accuracy Eval</h1>
          <p className="text-sm text-gray-500 mt-1">
            {queries.length} fixed test queries spanning every route. Run the full set
            to detect regressions and quantify accuracy across releases.
          </p>
        </div>
        <button
          onClick={handleStartRun}
          disabled={starting || currentRun?.status === 'running'}
          className="bg-kcc text-white hover:bg-kcc-dark px-4 py-2 text-xs rounded-lg font-medium transition-all disabled:opacity-50 flex items-center gap-1.5"
        >
          {currentRun?.status === 'running' ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {currentRun?.status === 'running' ? 'Running…' : 'Run eval'}
        </button>
      </div>

      {/* Current run summary */}
      {currentRun && (
        <div className="mb-6 bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <div className="text-xs text-gray-500">Run</div>
              <div className="font-mono text-sm">{currentRun.id}</div>
              <div className="text-xs text-gray-400 mt-0.5">
                Started {new Date(currentRun.started_at).toLocaleString()}
                {currentRun.finished_at && ` · Finished ${new Date(currentRun.finished_at).toLocaleString()}`}
              </div>
            </div>
            <button onClick={loadData} className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1">
              <RefreshCw size={12} /> Refresh
            </button>
          </div>
          <div className="grid grid-cols-5 gap-3">
            <MetricCard label="Total" value={currentRun.total.toString()} />
            <MetricCard label="Completed" value={`${currentRun.completed}/${currentRun.total}`} />
            <MetricCard label="Passed" value={currentRun.passed.toString()} color="green" />
            <MetricCard label="Failed" value={currentRun.failed.toString()} color="red" />
            <MetricCard label="Pass rate" value={passRate ? `${passRate}%` : '—'} color="blue" />
          </div>
          {currentRun.status === 'running' && (
            <div className="mt-3 h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-kcc transition-all"
                style={{ width: `${(currentRun.completed / currentRun.total) * 100}%` }}
              />
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200 mb-4 flex gap-4">
        {(['queries', 'runs'] as const).map(t => (
          <button
            key={t}
            onClick={() => setActiveTab(t)}
            className={`pb-2 text-sm font-medium border-b-2 transition-all ${
              activeTab === t ? 'border-kcc text-kcc' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t === 'queries' ? `Queries (${queries.length})` : `Runs (${runs.length})`}
          </button>
        ))}
        {currentRun?.results && currentRun.results.length > 0 && (
          <button
            onClick={() => setActiveTab('runs')}
            className={`pb-2 text-sm font-medium border-b-2 transition-all ${
              activeTab === 'runs' ? 'border-kcc text-kcc' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Latest results ({currentRun.results.length})
          </button>
        )}
      </div>

      {activeTab === 'queries' && (
        <div className="space-y-2">
          {queries.map(q => (
            <div key={q.id} className="bg-white border border-gray-200 rounded-lg p-3 text-sm">
              <div className="flex items-start gap-3">
                <span className="font-mono text-[11px] text-gray-400 w-20 shrink-0 mt-0.5">{q.id}</span>
                <span className={`text-[10px] uppercase tracking-wide font-semibold px-2 py-0.5 rounded ${CAT_COLORS[q.category] || 'bg-gray-100 text-gray-600'} shrink-0`}>
                  {q.category}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-gray-900">{q.query}</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">
                    expects: <span className="font-mono">{q.expected_route}</span>
                    {q.accept_routes && ` (or ${q.accept_routes.join(', ')})`}
                    {q.ground_truth_sql && ` · numeric check`}
                    {q.expected_substring && ` · substring "${q.expected_substring}"`}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {activeTab === 'runs' && currentRun?.results && currentRun.results.length > 0 ? (
        <div className="space-y-2">
          {currentRun.results.map(r => {
            const pass = r.route_match && (r.value_match === null || r.value_match === 1);
            return (
              <div key={r.query_id} className="bg-white border border-gray-200 rounded-lg p-3 text-sm">
                <div className="flex items-start gap-3">
                  <span className="shrink-0 mt-0.5">
                    {r.error ? (
                      <AlertTriangle size={14} className="text-amber-500" />
                    ) : pass ? (
                      <CheckCircle2 size={14} className="text-green-600" />
                    ) : (
                      <XCircle size={14} className="text-red-500" />
                    )}
                  </span>
                  <span className="font-mono text-[11px] text-gray-400 w-20 shrink-0 mt-0.5">{r.query_id}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-gray-900">{r.query_text}</div>
                    <div className="text-[11px] text-gray-500 mt-0.5">
                      <span className="font-mono">{r.actual_route || '∅'}</span> vs expected <span className="font-mono">{r.expected_route}</span>
                      {' · '}{r.duration_ms}ms
                      {r.expected_value !== null && ` · expected ${r.expected_value}, got ${r.actual_value ?? '?'}`}
                    </div>
                    {r.error && (
                      <div className="text-[11px] text-red-600 mt-1">error: {r.error}</div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : activeTab === 'runs' ? (
        <div className="space-y-2">
          {runs.length === 0 && <div className="text-sm text-gray-400">No runs yet. Click "Run eval" above to start one.</div>}
          {runs.map(r => (
            <button
              key={r.id}
              onClick={() => handleLoadRun(r.id)}
              className="w-full text-left bg-white border border-gray-200 rounded-lg p-3 text-sm hover:border-kcc transition-all"
            >
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-mono text-xs">{r.id}</span>
                  <span className="ml-3 text-xs text-gray-500">{new Date(r.started_at).toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-4 text-xs">
                  <span className="text-green-600">✓ {r.passed}</span>
                  <span className="text-red-500">✗ {r.failed}</span>
                  {r.errored > 0 && <span className="text-amber-600">! {r.errored}</span>}
                  <span className="text-gray-400">{r.completed}/{r.total}</span>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${r.status === 'running' ? 'bg-blue-50 text-blue-700' : 'bg-gray-100 text-gray-600'}`}>
                    {r.status}
                  </span>
                </div>
              </div>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
