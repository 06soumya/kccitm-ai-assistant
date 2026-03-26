'use client';
import { useState, useEffect } from 'react';
import { getPrompts, getPromptProposals, approvePromptProposal, rollbackPrompt } from '@/lib/adminApi';
import StatusBadge from '@/components/admin/StatusBadge';
import ActionButton from '@/components/admin/ActionButton';

export default function PromptsPage() {
  const [prompts, setPrompts] = useState<any[]>([]);
  const [proposals, setProposals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    Promise.all([getPrompts(), getPromptProposals()])
      .then(([p, pp]) => { setPrompts(p.prompts || []); setProposals(pp.proposals || []); })
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);

  if (loading) return <div className="text-sm text-gray-400">Loading prompts...</div>;

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">Prompt lab</h1>
      <h2 className="text-sm font-semibold mb-2">Active prompts</h2>
      {prompts.map((p: any, i: number) => (
        <div key={i} className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
          <div className="flex justify-between items-center mb-2">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">{p.prompt_name}/{p.section_name}</span>
              <StatusBadge status={`v${p.version || 1}`} />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-gray-400">{p.query_count || 0} queries</span>
              {(p.version || 1) > 1 && (
                <ActionButton label="Rollback" size="sm" onClick={async () => { await rollbackPrompt(p.prompt_name, p.section_name); load(); }} />
              )}
            </div>
          </div>
          <pre className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-[10px] font-mono max-h-28 overflow-y-auto whitespace-pre-wrap">{p.content?.slice(0, 500)}...</pre>
        </div>
      ))}
      {proposals.length > 0 && (
        <>
          <h2 className="text-sm font-semibold mt-5 mb-2">Pending proposals</h2>
          {proposals.map((p: any, i: number) => (
            <div key={i} className="bg-white border border-gray-200 rounded-xl p-4 mb-3">
              <StatusBadge status={`${p.prompt_name}/${p.section_name}`} />
              <div className="text-xs text-gray-500 mt-2 mb-2">{p.change_reason}</div>
              <div className="flex gap-2">
                <ActionButton label="Approve (start A/B)" variant="primary" onClick={async () => { await approvePromptProposal(p.id); load(); }} />
                <ActionButton label="Reject" variant="danger" onClick={async () => load()} />
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
