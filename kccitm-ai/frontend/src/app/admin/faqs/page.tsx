'use client';
import { useState, useEffect } from 'react';
import { getFAQs, updateFAQ, retireFAQ } from '@/lib/adminApi';
import StatusBadge from '@/components/admin/StatusBadge';
import ActionButton from '@/components/admin/ActionButton';
import MetricCard from '@/components/admin/MetricCard';

export default function FAQsPage() {
  const [faqs, setFaqs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<any>(null);

  const load = () => { getFAQs().then(d => setFaqs(d.faqs || [])).finally(() => setLoading(false)); };
  useEffect(() => { load(); }, []);

  if (loading) return <div className="text-sm text-gray-400">Loading FAQs...</div>;

  const verified = faqs.filter(f => f.admin_verified);
  const totalHits = faqs.reduce((s: number, f: any) => s + (f.hit_count || 0), 0);

  return (
    <div>
      <h1 className="text-lg font-bold mb-4">FAQ manager</h1>
      <div className="grid grid-cols-3 gap-3 mb-4">
        <MetricCard label="Total FAQs" value={faqs.length} />
        <MetricCard label="Total hits" value={totalHits} />
        <MetricCard label="Verified" value={verified.length} color="green" />
      </div>
      {faqs.map((faq: any, i: number) => (
        <div key={faq.id || i} className="bg-white border border-gray-200 rounded-xl p-4 mb-2">
          <div className="text-sm font-semibold mb-1">{faq.canonical_question}</div>
          <div className="text-xs text-gray-500 leading-relaxed mb-2">{faq.answer}</div>
          <div className="flex items-center gap-2 text-[10px] text-gray-400 flex-wrap">
            <StatusBadge status={faq.status || 'active'} />
            <StatusBadge status={faq.admin_verified ? 'verified' : 'unverified'} />
            <span>{faq.hit_count || 0} hits</span>
            <div className="ml-auto flex gap-1.5">
              {!faq.admin_verified && <ActionButton label="Verify" size="sm" variant="primary" onClick={async () => { await updateFAQ(faq.id, { question: faq.canonical_question, answer: faq.answer }); load(); }} />}
              <ActionButton label="Edit" size="sm" onClick={async () => setEditing(faq)} />
              <ActionButton label="Retire" size="sm" variant="danger" onClick={async () => { await retireFAQ(faq.id); load(); }} />
            </div>
          </div>
        </div>
      ))}
      {editing && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={() => setEditing(null)}>
          <div className="bg-white rounded-2xl p-6 max-w-lg w-full" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold mb-3">Edit FAQ</h3>
            <label className="text-[10px] text-gray-500">Question</label>
            <textarea className="w-full border border-gray-200 rounded-lg p-2 text-sm mb-3" rows={2} value={editing.canonical_question} onChange={e => setEditing({...editing, canonical_question: e.target.value})} />
            <label className="text-[10px] text-gray-500">Answer</label>
            <textarea className="w-full border border-gray-200 rounded-lg p-2 text-sm mb-4" rows={4} value={editing.answer} onChange={e => setEditing({...editing, answer: e.target.value})} />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setEditing(null)} className="px-4 py-2 text-xs border border-gray-200 rounded-lg">Cancel</button>
              <button onClick={async () => { await updateFAQ(editing.id, { question: editing.canonical_question, answer: editing.answer }); setEditing(null); load(); }} className="px-4 py-2 text-xs bg-kcc text-white rounded-lg font-medium">Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
