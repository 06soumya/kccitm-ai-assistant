'use client';
import { useState, useEffect } from 'react';
import { getSystemHealth, getCacheStats, clearCache, triggerHealing, triggerFAQGen, triggerPromptEvo, refreshSchema } from '@/lib/adminApi';
import ActionButton from '@/components/admin/ActionButton';
import ConfirmDialog from '@/components/admin/ConfirmDialog';

export default function SystemPage() {
  const [health, setHealth] = useState<any>(null);
  const [cache, setCache] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const [jobResult, setJobResult] = useState('');

  const load = () => {
    Promise.all([getSystemHealth(), getCacheStats()])
      .then(([h, c]) => { setHealth(h); setCache(c); })
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);

  if (loading) return <div className="text-sm text-gray-400">Loading system health...</div>;

  const services = health?.services || {};

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">System health</h1>
      <div className="grid grid-cols-3 gap-3 mb-5">
        {Object.entries(services).map(([name, info]: [string, any]) => (
          <div key={name} className="bg-white border border-gray-200 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <div className={`w-2.5 h-2.5 rounded-full ${info.status === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
              <span className="text-sm font-semibold capitalize">{name}</span>
            </div>
            <div className={`text-[10px] font-medium ${info.status === 'ok' ? 'text-green-600' : 'text-red-600'}`}>
              {info.status === 'ok' ? 'Healthy' : info.message || 'Error'}
            </div>
          </div>
        ))}
      </div>

      <h2 className="text-sm font-semibold mb-3">Batch job controls</h2>
      <div className="grid grid-cols-3 gap-3 mb-5">
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-sm font-semibold">Daily healing</div>
          <div className="text-[10px] text-gray-500 mt-1 mb-3">Runs at 2:00 AM daily</div>
          <ActionButton label="Run now" variant="primary" onClick={async () => { const r = await triggerHealing(); setJobResult(r.message || 'Done'); }} />
        </div>
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-sm font-semibold">FAQ generation</div>
          <div className="text-[10px] text-gray-500 mt-1 mb-3">Runs at 3:00 AM daily</div>
          <ActionButton label="Run now" variant="primary" onClick={async () => { const r = await triggerFAQGen(); setJobResult(r.message || 'Done'); }} />
        </div>
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="text-sm font-semibold">Prompt evolution</div>
          <div className="text-[10px] text-gray-500 mt-1 mb-3">Runs Sunday 3:00 AM</div>
          <ActionButton label="Run now" variant="primary" onClick={async () => { const r = await triggerPromptEvo(); setJobResult(r.message || 'Done'); }} />
        </div>
      </div>
      {jobResult && <div className="mb-4 px-3 py-2 bg-green-50 border border-green-200 rounded-lg text-xs text-green-700">{jobResult}</div>}

      <h2 className="text-sm font-semibold mb-3">Cache & Schema</h2>
      <div className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-4 flex-wrap">
        <span className="text-sm">{cache?.active_entries || 0} entries · {cache?.total_hits || 0} hits</span>
        <ActionButton label="Clear all cache" variant="danger" onClick={async () => setShowClearConfirm(true)} />
        <ActionButton label="Refresh DB schema" variant="primary" onClick={async () => { const r = await refreshSchema(); setJobResult(`Schema refreshed: ${r.tables} tables, ${r.foreign_keys} FKs`); }} />
      </div>
      {showClearConfirm && (
        <ConfirmDialog
          title="Clear cache?"
          message="This will remove all cached responses. Queries will take longer until the cache rebuilds."
          confirmLabel="Clear cache"
          variant="danger"
          onConfirm={async () => { await clearCache(); setShowClearConfirm(false); load(); }}
          onCancel={() => setShowClearConfirm(false)}
        />
      )}
    </div>
  );
}
