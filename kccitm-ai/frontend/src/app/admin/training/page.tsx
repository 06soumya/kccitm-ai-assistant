'use client';
import { useState, useEffect } from 'react';
import { getTrainingStats, getTrainingCandidates, exportTrainingData } from '@/lib/adminApi';
import MetricCard from '@/components/admin/MetricCard';
import ActionButton from '@/components/admin/ActionButton';

export default function TrainingPage() {
  const [stats, setStats] = useState<any>(null);
  const [candidates, setCandidates] = useState<any[]>([]);
  const [exportResult, setExportResult] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getTrainingStats(), getTrainingCandidates()])
      .then(([s, c]) => { setStats(s); setCandidates(c.candidates || []); })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-sm text-gray-400">Loading training data...</div>;

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">Training data</h1>
      <div className="grid grid-cols-4 gap-3 mb-4">
        <MetricCard label="Candidates" value={stats?.total_candidates || 0} />
        <MetricCard label="LoRA ready" value={stats?.ready_for_lora ? 'Yes' : 'No'} color={stats?.ready_for_lora ? 'green' : 'amber'} subtitle={stats?.ready_for_lora ? '' : `Need ${500 - (stats?.total_candidates || 0)} more`} />
        <MetricCard label="From feedback" value={stats?.by_category?.filter((c: any) => c.source === 'feedback_positive').reduce((s: number, c: any) => s + c.cnt, 0) || 0} />
        <MetricCard label="From FAQs" value={stats?.by_category?.filter((c: any) => c.source === 'faq').reduce((s: number, c: any) => s + c.cnt, 0) || 0} />
      </div>
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <div className="flex justify-between items-center mb-3">
          <h3 className="text-sm font-semibold">Actions</h3>
          <div className="flex gap-2">
            <ActionButton label="Export JSONL" variant="primary" onClick={async () => { const r = await exportTrainingData(); setExportResult(r); }} />
            <ActionButton label="Start training (manual)" onClick={async () => alert('Run: python -m training.train_lora')} />
          </div>
        </div>
        {exportResult && <div className="text-xs text-green-600 mt-2">Exported {exportResult.total} entries</div>}
      </div>
      {candidates.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <h3 className="text-sm font-semibold mb-3">Recent candidates</h3>
          <table className="w-full text-xs">
            <thead><tr className="border-b border-gray-200"><th className="text-left py-2 text-[10px] text-gray-500">Query</th><th className="py-2 text-[10px] text-gray-500">Category</th><th className="py-2 text-[10px] text-gray-500">Score</th><th className="py-2 text-[10px] text-gray-500">Source</th></tr></thead>
            <tbody>
              {candidates.slice(0, 15).map((c: any, i: number) => (
                <tr key={i} className="border-b border-gray-100">
                  <td className="py-2 max-w-[200px] truncate">{c.query}</td>
                  <td className="py-2"><span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">{c.category}</span></td>
                  <td className="py-2 text-green-600">{c.quality_score?.toFixed(2)}</td>
                  <td className="py-2 text-[10px] text-gray-400">{c.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
