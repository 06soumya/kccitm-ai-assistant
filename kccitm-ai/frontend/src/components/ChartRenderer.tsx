'use client';
import {
  ResponsiveContainer,
  LineChart, Line,
  BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts';

export interface ChartData {
  type: 'line' | 'bar';
  title: string;
  xKey: string;
  yKeys: { key: string; label: string; color: string }[];
  data: Record<string, unknown>[];
}

const COLORS = ['#2563eb', '#dc2626', '#16a34a', '#d97706', '#7c3aed', '#0891b2'];

export default function ChartRenderer({ chart }: { chart: ChartData }) {
  if (!chart?.data?.length || !chart.yKeys?.length) return null;

  const ChartComponent = chart.type === 'bar' ? BarChart : LineChart;

  return (
    <div className="my-3 p-3 bg-gray-50 rounded-xl border border-gray-200">
      {chart.title && (
        <p className="text-xs font-semibold text-gray-600 mb-2">{chart.title}</p>
      )}
      <ResponsiveContainer width="100%" height={220}>
        <ChartComponent data={chart.data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey={chart.xKey}
            tick={{ fontSize: 11 }}
            stroke="#6b7280"
          />
          <YAxis tick={{ fontSize: 11 }} stroke="#6b7280" />
          <Tooltip
            contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #e5e7eb' }}
          />
          {chart.yKeys.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
          {chart.yKeys.map((yk, i) =>
            chart.type === 'bar' ? (
              <Bar
                key={yk.key}
                dataKey={yk.key}
                name={yk.label}
                fill={yk.color || COLORS[i % COLORS.length]}
                radius={[4, 4, 0, 0]}
              />
            ) : (
              <Line
                key={yk.key}
                type="monotone"
                dataKey={yk.key}
                name={yk.label}
                stroke={yk.color || COLORS[i % COLORS.length]}
                strokeWidth={2}
                dot={{ r: 4 }}
                activeDot={{ r: 6 }}
              />
            )
          )}
        </ChartComponent>
      </ResponsiveContainer>
    </div>
  );
}
