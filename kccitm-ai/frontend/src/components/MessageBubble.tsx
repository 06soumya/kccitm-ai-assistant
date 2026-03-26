'use client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import FeedbackButtons from './FeedbackButtons';
import PipelineIndicator from './PipelineIndicator';
import type { Message } from '@/lib/types';

export default function MessageBubble({ message, sessionId }: { message: Message; sessionId: string }) {
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
        </div>
        <FeedbackButtons messageId={message.id} sessionId={sessionId} />
      </div>
    </div>
  );
}
