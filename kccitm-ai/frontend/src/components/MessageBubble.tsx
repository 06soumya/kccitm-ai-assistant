'use client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import FeedbackButtons from './FeedbackButtons';
import PipelineIndicator from './PipelineIndicator';
import ChartRenderer from './ChartRenderer';
import type { ChartData } from './ChartRenderer';
import type { Message } from '@/lib/types';

interface MessageBubbleProps {
  message: Message;
  sessionId: string;
  onSelectOption?: (optionText: string) => void;
}

export default function MessageBubble({ message, sessionId, onSelectOption }: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const meta = message.metadata;

  if (isUser) {
    return (
      <div className="flex justify-end mb-4 animate-fadeUp">
        <div className="max-w-[72%]">
          <div className="px-4 py-2.5 bg-kcc text-white rounded-2xl rounded-tr-sm text-sm leading-relaxed">
            {message.content}
          </div>
        </div>
      </div>
    );
  }

  const chartData = meta?.chart_data as ChartData | undefined;
  const showClarification = !!meta?.needs_clarification && Array.isArray(meta?.clarification_options) && (meta?.clarification_options?.length ?? 0) > 0;
  const clarificationOptions: string[] = showClarification ? (meta?.clarification_options as string[]) : [];

  return (
    <div className="flex gap-3 mb-4 animate-fadeUp">
      <div className="w-8 h-7 rounded-md flex-shrink-0 shadow-sm bg-kcc-bg flex items-center justify-center">
        <span className="text-kcc text-xs font-bold">K</span>
      </div>
      <div className="max-w-[75%]">
        {meta?.route_used && (
          <div className="mb-1">
            <PipelineIndicator route={meta.route_used} timeMs={meta.total_time_ms} />
          </div>
        )}
        <div className="px-4 py-3 bg-white border border-gray-200 rounded-2xl rounded-tl-sm text-sm leading-relaxed prose">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          {chartData && <ChartRenderer chart={chartData} />}
          {showClarification && onSelectOption && (
            <div className="not-prose mt-3 flex flex-col gap-2">
              {clarificationOptions.map((opt, i) => (
                <button
                  key={i}
                  onClick={() => onSelectOption(opt)}
                  className="text-left px-3 py-2 border border-kcc/30 bg-kcc-bg/40 hover:bg-kcc-bg hover:border-kcc rounded-lg text-sm text-gray-800 transition-all"
                >
                  <span className="text-kcc font-semibold mr-2">{i + 1}.</span>
                  {opt}
                </button>
              ))}
            </div>
          )}
        </div>
        <FeedbackButtons messageId={message.id} sessionId={sessionId} />
      </div>
    </div>
  );
}
