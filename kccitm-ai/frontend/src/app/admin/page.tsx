'use client';
import { useState, useEffect } from 'react';
import { getDashboardMetrics, getQualityStats, getCacheStats } from '@/lib/adminApi';
import MetricCard from '@/components/admin/MetricCard';

export default function AdminOverview() {
  const [metrics, setMetrics] = useState<any>(null);
  const [quality, setQuality] = useState<any>(null);
  const [cache, setCache] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    Promise.all([getDashboardMetrics(), getQualityStats(), getCacheStats()])
      .then(([m, q, c]) => { setMetrics(m); setQuality(q); setCache(c); })
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, []);

  if (loading) return <div className="text-sm text-gray-400">Loading dashboard...</div>;

  const sd = quality?.score_distribution || {};
  const avgScore = sd.overall_avg || 0;

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">Overview</h1>
      <div className="grid grid-cols-4 gap-3 mb-4">
        <MetricCard label="Users" value={metrics?.users || 0} />
        <MetricCard label="Sessions" value={metrics?.sessions || 0} />
        <MetricCard label="Messages" value={metrics?.messages || 0} />
        <MetricCard label="Avg quality" value={avgScore.toFixed(2)} color={avgScore >= 0.7 ? 'green' : 'amber'} />
      </div>
      <div className="grid grid-cols-3 gap-3 mb-4">
        <MetricCard label="Cache entries" value={cache?.active_entries || 0} subtitle={`${cache?.total_hits || 0} hits`} />
        <MetricCard label="Active FAQs" value={metrics?.faqs || '—'} />
        <MetricCard label="Training candidates" value="—" subtitle="Check training page" />
      </div>
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <h3 className="text-sm font-semibold mb-3">Quality distribution</h3>
        <div className="flex h-7 rounded-lg overflow-hidden gap-0.5">
          <div className="bg-green-100 text-green-700 flex items-center justify-center text-[10px] font-semibold" style={{flex: (sd.high || 0) + (sd.very_high || 0)}}>Good {(sd.high || 0) + (sd.very_high || 0)}</div>
          <div className="bg-blue-100 text-blue-700 flex items-center justify-center text-[10px] font-semibold" style={{flex: sd.medium || 0}}>OK {sd.medium || 0}</div>
          <div className="bg-amber-100 text-amber-700 flex items-center justify-center text-[10px] font-semibold" style={{flex: sd.low || 0}}>Poor {sd.low || 0}</div>
          <div className="bg-red-100 text-red-700 flex items-center justify-center text-[10px] font-semibold" style={{flex: sd.very_low || 0}}>Fail {sd.very_low || 0}</div>
        </div>
      </div>
      {cache?.top_queries?.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <h3 className="text-sm font-semibold mb-3">Top cached queries</h3>
          <table className="w-full text-xs">
            <thead><tr className="border-b border-gray-200"><th className="text-left py-2 text-[10px] font-medium text-gray-500">Query</th><th className="text-left py-2 text-[10px] font-medium text-gray-500">Route</th><th className="text-right py-2 text-[10px] font-medium text-gray-500">Hits</th></tr></thead>
            <tbody>
              {cache.top_queries.map((q: any, i: number) => (
                <tr key={i} className="border-b border-gray-100">
                  <td className="py-2">{q.query_text}</td>
                  <td className="py-2"><span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">{q.route_used}</span></td>
                  <td className="py-2 text-right">{q.hit_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
