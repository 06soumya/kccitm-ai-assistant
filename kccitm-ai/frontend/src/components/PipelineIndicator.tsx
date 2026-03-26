'use client';

const ROUTE_COLORS: Record<string, string> = {
  SQL: 'bg-blue-50 text-blue-700',
  RAG: 'bg-green-50 text-green-700',
  HYBRID: 'bg-purple-50 text-purple-700',
  FAQ: 'bg-amber-50 text-amber-700',
  CACHED: 'bg-emerald-50 text-emerald-700',
};

export default function PipelineIndicator({ route, timeMs }: { route: string; timeMs?: number }) {
  const key = Object.keys(ROUTE_COLORS).find(k => route.toUpperCase().includes(k)) || '';
  const color = ROUTE_COLORS[key] || 'bg-gray-100 text-gray-600';

  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full ${color}`}>
      {route}
      {timeMs != null && <span className="opacity-60">{(timeMs / 1000).toFixed(1)}s</span>}
    </span>
  );
}
