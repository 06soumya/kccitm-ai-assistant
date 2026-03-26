'use client';
import { useState, useEffect } from 'react';
import { getFeedbackQueue, getImplicitSignals } from '@/lib/adminApi';
import StatusBadge from '@/components/admin/StatusBadge';

export default function FeedbackPage() {
  const [feedback, setFeedback] = useState<any[]>([]);
  const [signals, setSignals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getFeedbackQueue(), getImplicitSignals()])
      .then(([f, s]) => { setFeedback(f.feedback || []); setSignals(s.signals || []); })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-sm text-gray-400">Loading feedback...</div>;

  const negative = feedback.filter(f => f.rating <= 2);
  const positive = feedback.filter(f => f.rating >= 4);

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">Feedback queue</h1>
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="bg-white border border-gray-200 rounded-xl p-4"><div className="text-[10px] text-gray-500">Total</div><div className="text-2xl font-bold mt-1">{feedback.length}</div></div>
        <div className="bg-white border border-gray-200 rounded-xl p-4"><div className="text-[10px] text-gray-500">Positive</div><div className="text-2xl font-bold mt-1 text-green-600">{positive.length}</div></div>
        <div className="bg-white border border-gray-200 rounded-xl p-4"><div className="text-[10px] text-gray-500">Negative</div><div className="text-2xl font-bold mt-1 text-kcc">{negative.length}</div></div>
      </div>
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <h3 className="text-sm font-semibold mb-3">Recent feedback</h3>
        {feedback.length === 0 ? <p className="text-xs text-gray-400">No feedback yet</p> : (
          <table className="w-full text-xs">
            <thead><tr className="border-b border-gray-200"><th className="text-left py-2 text-[10px] text-gray-500">Query</th><th className="py-2 text-[10px] text-gray-500">Rating</th><th className="py-2 text-[10px] text-gray-500">Quality</th><th className="py-2 text-[10px] text-gray-500">Route</th><th className="py-2 text-[10px] text-gray-500">Comment</th></tr></thead>
            <tbody>
              {feedback.slice(0, 20).map((f: any, i: number) => (
                <tr key={i} className="border-b border-gray-100">
                  <td className="py-2 max-w-[200px] truncate">{f.query_text || '—'}</td>
                  <td className="py-2 text-center">{f.rating >= 4 ? '👍' : '👎'}</td>
                  <td className="py-2 text-center" style={{color: f.quality_score < 0.3 ? '#dc2626' : f.quality_score < 0.5 ? '#d97706' : '#16a34a'}}>{f.quality_score?.toFixed(2) || '—'}</td>
                  <td className="py-2"><StatusBadge status={f.route_used || '—'} /></td>
                  <td className="py-2 text-gray-400 max-w-[150px] truncate">{f.feedback_text || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <h3 className="text-sm font-semibold mb-3">Implicit signals</h3>
        {signals.length === 0 ? <p className="text-xs text-gray-400">No signals detected yet</p> : (
          <table className="w-full text-xs">
            <thead><tr className="border-b border-gray-200"><th className="text-left py-2 text-[10px] text-gray-500">Type</th><th className="py-2 text-[10px] text-gray-500">Original</th><th className="py-2 text-[10px] text-gray-500">Follow-up</th></tr></thead>
            <tbody>
              {signals.slice(0, 15).map((s: any, i: number) => (
                <tr key={i} className="border-b border-gray-100">
                  <td className="py-2"><StatusBadge status={s.signal_type} /></td>
                  <td className="py-2 text-[10px]">{s.original_query || '—'}</td>
                  <td className="py-2 text-[10px]">{s.follow_up_query || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
