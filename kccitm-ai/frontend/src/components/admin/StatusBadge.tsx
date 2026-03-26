'use client';

const COLORS: Record<string, string> = {
  active: 'bg-green-50 text-green-700',
  ok: 'bg-green-50 text-green-700',
  verified: 'bg-green-50 text-green-700',
  pending: 'bg-amber-50 text-amber-700',
  unverified: 'bg-amber-50 text-amber-700',
  medium: 'bg-amber-50 text-amber-700',
  rejected: 'bg-red-50 text-red-700',
  retired: 'bg-gray-100 text-gray-500',
  high: 'bg-red-50 text-red-700',
  low: 'bg-green-50 text-green-700',
  rephrase: 'bg-red-50 text-red-700',
  follow_up: 'bg-green-50 text-green-700',
  abandon: 'bg-amber-50 text-amber-700',
  long_session: 'bg-blue-50 text-blue-700',
  SQL: 'bg-blue-50 text-blue-700',
  RAG: 'bg-green-50 text-green-700',
  HYBRID: 'bg-purple-50 text-purple-700',
  base: 'bg-blue-50 text-blue-700',
  'fine-tuned': 'bg-purple-50 text-purple-700',
};

export default function StatusBadge({ status }: { status: string }) {
  const color = COLORS[status] || 'bg-gray-100 text-gray-600';
  return (
    <span className={`inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full ${color}`}>
      {status}
    </span>
  );
}
