'use client';
import { useState, useEffect } from 'react';
import { getTrainingCandidates, exportTrainingData, getStarStats, addTrainingPair } from '@/lib/adminApi';
import MetricCard from '@/components/admin/MetricCard';
import ActionButton from '@/components/admin/ActionButton';

export default function TrainingPage() {
  const [stats, setStats] = useState<any>(null);
  const [candidates, setCandidates] = useState<any[]>([]);
  const [exportResult, setExportResult] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  // Add training pair form
  const [query, setQuery] = useState('');
  const [correctSql, setCorrectSql] = useState('');
  const [addResult, setAddResult] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const [adding, setAdding] = useState(false);

  const loadData = () => {
    Promise.all([getStarStats(), getTrainingCandidates()])
      .then(([s, c]) => { setStats(s); setCandidates(c.candidates || []); })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadData(); }, []);

  const handleAdd = async () => {
    if (!query.trim() || !correctSql.trim()) {
      setAddResult({ type: 'error', msg: 'Both query and correct SQL are required.' });
      return;
    }
    setAdding(true);
    setAddResult(null);
    try {
      const r = await addTrainingPair(query.trim(), correctSql.trim());
      if (r.success) {
        setAddResult({ type: 'success', msg: `Training pair added! Total: ${r.stats?.total || '?'}/500` });
        setQuery('');
        setCorrectSql('');
        setStats(r.stats);
        loadData();
      } else {
        setAddResult({ type: 'error', msg: r.error || 'Failed to add training pair.' });
      }
    } catch (err: any) {
      setAddResult({ type: 'error', msg: err.message || 'Request failed.' });
    } finally {
      setAdding(false);
    }
  };

  if (loading) return <div className="text-sm text-gray-400">Loading training data...</div>;

  const total = stats?.total || 0;
  const target = stats?.target || 500;
  const pct = stats?.progress_pct || 0;
  const bySource = stats?.by_source || {};

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">Training data</h1>

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        <MetricCard label="Candidates" value={total} subtitle={`/ ${target} target`} />
        <MetricCard
          label="LoRA ready"
          value={stats?.ready_for_lora ? 'Yes' : 'No'}
          color={stats?.ready_for_lora ? 'green' : 'amber'}
          subtitle={stats?.ready_for_lora ? 'Ready to fine-tune' : `Need ${target - total} more`}
        />
        <MetricCard label="STaR pairs" value={(bySource['star_rationalization'] || 0) + (bySource['star_success'] || 0)} />
        <MetricCard label="Manual seeds" value={bySource['manual_seed'] || 0} />
      </div>

      {/* Progress bar */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <div className="flex justify-between items-center mb-2">
          <h3 className="text-sm font-semibold">Progress to LoRA fine-tuning</h3>
          <span className="text-xs text-gray-500">{total} / {target} ({pct}%)</span>
        </div>
        <div className="w-full h-3 bg-gray-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${pct >= 100 ? 'bg-green-500' : pct >= 50 ? 'bg-blue-500' : 'bg-amber-500'}`}
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
        <div className="flex flex-wrap gap-3 mt-3 text-[10px] text-gray-500">
          {Object.entries(bySource).map(([src, count]) => (
            <span key={src} className="px-2 py-0.5 bg-gray-50 rounded-full">
              {src}: {count as number}
            </span>
          ))}
        </div>
      </div>

      {/* Add Training Pair form */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <h3 className="text-sm font-semibold mb-3">Add training pair (STaR-SQL)</h3>
        <p className="text-[10px] text-gray-400 mb-3">
          Provide a query and the correct SQL. The system will generate a reasoning chain and save it as a training candidate.
        </p>
        <label className="text-[10px] text-gray-500 font-medium">Query</label>
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="e.g., top 5 students by SGPA in semester 1"
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 outline-none focus:border-kcc focus:bg-white transition-all mb-3 mt-1"
        />
        <label className="text-[10px] text-gray-500 font-medium">Correct SQL</label>
        <textarea
          value={correctSql}
          onChange={e => setCorrectSql(e.target.value)}
          placeholder="SELECT s.name, sr.sgpa FROM students s JOIN semester_results sr ON s.roll_no = sr.roll_no WHERE sr.semester = 1 ORDER BY sr.sgpa DESC LIMIT 5"
          rows={3}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 outline-none focus:border-kcc focus:bg-white transition-all mb-3 mt-1 font-mono text-xs"
        />
        <div className="flex items-center gap-3">
          <ActionButton label={adding ? 'Adding...' : 'Add Training Pair'} variant="primary" onClick={handleAdd} />
          {addResult && (
            <span className={`text-xs ${addResult.type === 'success' ? 'text-green-600' : 'text-red-600'}`}>
              {addResult.msg}
            </span>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <div className="flex justify-between items-center">
          <h3 className="text-sm font-semibold">Export & Train</h3>
          <div className="flex gap-2">
            <ActionButton label="Export JSONL" variant="primary" onClick={async () => { const r = await exportTrainingData(); setExportResult(r); }} />
            <ActionButton label="Start training (manual)" onClick={async () => alert('Run: python -m training.train_lora')} />
          </div>
        </div>
        {exportResult && <div className="text-xs text-green-600 mt-2">Exported {exportResult.total} entries</div>}
      </div>

      {/* Recent candidates */}
      {candidates.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <h3 className="text-sm font-semibold mb-3">Recent candidates</h3>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left py-2 text-[10px] text-gray-500">Query</th>
                <th className="py-2 text-[10px] text-gray-500">Category</th>
                <th className="py-2 text-[10px] text-gray-500">Score</th>
                <th className="py-2 text-[10px] text-gray-500">Source</th>
              </tr>
            </thead>
            <tbody>
              {candidates.slice(0, 15).map((c: any, i: number) => (
                <tr key={i} className="border-b border-gray-100">
                  <td className="py-2 max-w-[200px] truncate">{c.query}</td>
                  <td className="py-2">
                    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">{c.category}</span>
                  </td>
                  <td className="py-2 text-green-600">{c.quality_score?.toFixed(2)}</td>
                  <td className="py-2">
                    <span className={`text-[10px] px-2 py-0.5 rounded-full ${
                      c.source?.includes('star') ? 'bg-purple-50 text-purple-700' :
                      c.source === 'manual_seed' ? 'bg-amber-50 text-amber-700' :
                      'bg-gray-50 text-gray-600'
                    }`}>
                      {c.source}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
