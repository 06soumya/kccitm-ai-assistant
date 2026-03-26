'use client';

const COLORS: Record<string, string> = {
  green: 'text-green-600',
  red: 'text-red-600',
  amber: 'text-amber-600',
  blue: 'text-blue-600',
};

export default function MetricCard({ label, value, subtitle, color }: {
  label: string; value: string | number; subtitle?: string; color?: string;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="text-[10px] text-gray-500 font-medium uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-bold mt-1 ${color ? COLORS[color] || '' : ''}`}>{value}</div>
      {subtitle && <div className="text-[10px] text-gray-400 mt-0.5">{subtitle}</div>}
    </div>
  );
}
