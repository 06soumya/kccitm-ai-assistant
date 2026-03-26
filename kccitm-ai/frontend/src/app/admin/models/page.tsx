'use client';
import { useState, useEffect } from 'react';
import { getModels, switchModel } from '@/lib/adminApi';
import MetricCard from '@/components/admin/MetricCard';
import StatusBadge from '@/components/admin/StatusBadge';
import ActionButton from '@/components/admin/ActionButton';
import ConfirmDialog from '@/components/admin/ConfirmDialog';

export default function ModelsPage() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [switching, setSwitching] = useState<string | null>(null);

  const load = () => { getModels().then(setData).finally(() => setLoading(false)); };
  useEffect(() => { load(); }, []);

  if (loading) return <div className="text-sm text-gray-400">Loading models...</div>;

  const models = data?.models || [];
  const active = data?.active || '—';

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">Model manager</h1>
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-white border-2 border-kcc rounded-xl p-4">
          <div className="text-[10px] text-gray-500">Active model</div>
          <div className="text-lg font-bold mt-1">{active}</div>
        </div>
        <MetricCard label="Versions available" value={models.length} />
      </div>
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <h3 className="text-sm font-semibold mb-3">All versions</h3>
        <table className="w-full text-xs">
          <thead><tr className="border-b border-gray-200"><th className="text-left py-2 text-[10px] text-gray-500">Model</th><th className="py-2 text-[10px] text-gray-500">Type</th><th className="py-2 text-[10px] text-gray-500">Status</th><th></th></tr></thead>
          <tbody>
            {models.map((m: any, i: number) => (
              <tr key={i} className="border-b border-gray-100">
                <td className="py-2 font-medium">{m.model_name}</td>
                <td className="py-2"><StatusBadge status={m.type} /></td>
                <td className="py-2">{m.is_active ? <StatusBadge status="active" /> : <StatusBadge status="retired" />}</td>
                <td className="py-2 text-right">{!m.is_active && <ActionButton label="Activate" size="sm" variant="primary" onClick={async () => setSwitching(m.model_name)} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-[10px] text-amber-600 mt-3">Switching models requires a server restart.</p>
      </div>
      {switching && (
        <ConfirmDialog
          title="Switch model?"
          message={`Activate ${switching}? The server needs a restart for this to take effect.`}
          confirmLabel="Switch"
          variant="primary"
          onConfirm={async () => { await switchModel(switching); setSwitching(null); load(); }}
          onCancel={() => setSwitching(null)}
        />
      )}
    </div>
  );
}
