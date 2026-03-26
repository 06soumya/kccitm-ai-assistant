'use client';
import { useState, useEffect } from 'react';
import { getHealingQueue, approveHealingFix, rejectHealingFix } from '@/lib/adminApi';
import StatusBadge from '@/components/admin/StatusBadge';
import ActionButton from '@/components/admin/ActionButton';

export default function HealingPage() {
  const [queue, setQueue] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);

  const load = () => {
    setLoading(true);
    getHealingQueue(showAll ? 'all' : undefined)
      .then(d => setQueue(d.queue || []))
      .catch(() => setQueue([]))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, [showAll]);

  if (loading) return <div className="text-sm text-gray-400">Loading healing queue...</div>;

  const pending = queue.filter(f => f.status === 'pending');
  const resolved = queue.filter(f => f.status !== 'pending');

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <h1 className="text-lg font-bold">Healing queue</h1>
        <span className="text-[10px] font-semibold px-2.5 py-0.5 rounded-full bg-amber-50 text-amber-700">
          {pending.length} pending
        </span>
        <button
          onClick={() => setShowAll(!showAll)}
          className="text-[10px] text-kcc hover:underline ml-auto"
        >
          {showAll ? 'Show pending only' : 'Show all (including resolved)'}
        </button>
      </div>

      {queue.length === 0 ? (
        <div className="bg-white border border-gray-200 rounded-xl p-8 text-center text-sm text-gray-400">
          {showAll ? 'No healing entries yet. Submit thumbs-down feedback to populate this queue.' : 'No pending fixes — system is healthy!'}
        </div>
      ) : queue.map((fix: any, i: number) => (
        <div key={fix.id || i} className={`bg-white border rounded-xl p-4 mb-3 ${fix.status === 'pending' ? 'border-amber-200' : 'border-gray-200 opacity-60'}`}>
          <div className="flex gap-1.5 mb-2 flex-wrap">
            <StatusBadge status={fix.failure_type || 'unknown'} />
            <StatusBadge status={fix.risk_level || 'medium'} />
            <StatusBadge status={fix.fix_type || '—'} />
            {fix.status !== 'pending' && <StatusBadge status={fix.status} />}
          </div>
          <div className="text-xs text-gray-500 mb-1">Query:</div>
          <div className="text-sm italic mb-2">{fix.query || '—'}</div>
          <div className="text-xs text-gray-500 mb-1">Reason:</div>
          <div className="text-[10px] text-gray-400 mb-3">{fix.change_reason || '—'}</div>
          {fix.status === 'pending' && (
            <div className="flex gap-2">
              <ActionButton label="Approve" variant="primary" onClick={async () => { await approveHealingFix(fix.id); load(); }} />
              <ActionButton label="Reject" variant="danger" onClick={async () => { await rejectHealingFix(fix.id); load(); }} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
