'use client';
import { useState } from 'react';
import { Loader2 } from 'lucide-react';

const VARIANTS: Record<string, string> = {
  primary: 'bg-kcc text-white hover:bg-kcc-dark',
  danger: 'bg-red-500 text-white hover:bg-red-600',
  secondary: 'bg-white text-gray-600 border border-gray-200 hover:bg-gray-50',
};

export default function ActionButton({ label, onClick, variant = 'secondary', size = 'md' }: {
  label: string; onClick: () => Promise<void>; variant?: string; size?: string;
}) {
  const [loading, setLoading] = useState(false);

  const handle = async () => {
    setLoading(true);
    try { await onClick(); } finally { setLoading(false); }
  };

  const sizeClass = size === 'sm' ? 'px-2.5 py-1 text-[10px]' : 'px-4 py-2 text-xs';

  return (
    <button onClick={handle} disabled={loading}
      className={`${VARIANTS[variant] || VARIANTS.secondary} ${sizeClass} rounded-lg font-medium transition-all disabled:opacity-50 flex items-center gap-1.5`}>
      {loading && <Loader2 size={12} className="animate-spin" />}
      {label}
    </button>
  );
}
