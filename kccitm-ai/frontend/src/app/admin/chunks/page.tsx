'use client';
import { useState, useEffect } from 'react';
import { getChunkHealth } from '@/lib/adminApi';
import MetricCard from '@/components/admin/MetricCard';

export default function ChunksPage() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { getChunkHealth().then(setData).finally(() => setLoading(false)); }, []);

  if (loading) return <div className="text-sm text-gray-400">Loading chunk health...</div>;

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">Chunk health</h1>
      <div className="grid grid-cols-3 gap-3 mb-4">
        <MetricCard label="Tracked" value={data?.total_tracked || 0} />
        <MetricCard label="Underperforming" value={data?.underperforming_count || 0} color="red" subtitle="ratio < 20%" />
        <MetricCard label="Never retrieved" value={data?.never_retrieved_count || 0} color="amber" />
      </div>
      {data?.top_chunks?.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
          <h3 className="text-sm font-semibold mb-3">Top performing chunks</h3>
          <table className="w-full text-xs">
            <thead><tr className="border-b border-gray-200"><th className="text-left py-2 text-[10px] text-gray-500">Chunk</th><th className="py-2 text-[10px] text-gray-500">Retrieved</th><th className="py-2 text-[10px] text-gray-500">Top 5</th><th className="py-2 text-[10px] text-gray-500">Score</th></tr></thead>
            <tbody>
              {data.top_chunks.map((c: any, i: number) => (
                <tr key={i} className="border-b border-gray-100">
                  <td className="py-2 font-mono text-[9px]">{c.chunk_id?.slice(0, 25)}...</td>
                  <td className="py-2 text-center">{c.times_retrieved}</td>
                  <td className="py-2 text-center">{c.times_reranked_top5}</td>
                  <td className="py-2 text-center">{c.avg_reranker_score?.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[10px] text-gray-400">Chunks with low retrieval-to-rerank ratios need re-chunking.</p>
    </div>
  );
}
