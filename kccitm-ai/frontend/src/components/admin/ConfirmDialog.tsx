'use client';
import { useState } from 'react';
import { Loader2 } from 'lucide-react';

export default function ConfirmDialog({ title, message, confirmLabel = 'Confirm', variant = 'danger', onConfirm, onCancel }: {
  title: string; message: string; confirmLabel?: string; variant?: string;
  onConfirm: () => Promise<void>; onCancel: () => void;
}) {
  const [loading, setLoading] = useState(false);

  const handle = async () => {
    setLoading(true);
    try { await onConfirm(); } finally { setLoading(false); }
  };

  const btnColor = variant === 'danger' ? 'bg-red-500 hover:bg-red-600' : 'bg-kcc hover:bg-kcc-dark';

  return (
    <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center" onClick={onCancel}>
      <div className="bg-white rounded-2xl p-6 max-w-sm w-full shadow-xl" onClick={e => e.stopPropagation()}>
        <h3 className="text-sm font-semibold mb-2">{title}</h3>
        <p className="text-xs text-gray-500 mb-5">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-4 py-2 text-xs border border-gray-200 rounded-lg hover:bg-gray-50">Cancel</button>
          <button onClick={handle} disabled={loading}
            className={`px-4 py-2 text-xs text-white rounded-lg font-medium ${btnColor} disabled:opacity-50 flex items-center gap-1.5`}>
            {loading && <Loader2 size={12} className="animate-spin" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
