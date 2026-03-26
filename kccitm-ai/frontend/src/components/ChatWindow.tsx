'use client';
import { useRef, useEffect, useState } from 'react';
import { useChat } from '@/hooks/useChat';
import MessageBubble from './MessageBubble';
import PipelineIndicator from './PipelineIndicator';
import { Send, Loader2, Square } from 'lucide-react';
import type { Message } from '@/lib/types';

export default function ChatWindow({ sessionId, initialMessages = [] }: { sessionId: string; initialMessages?: Message[] }) {
  const { messages, isLoading, streamingText, currentRoute, error, send, stop } = useChat({
    sessionId, initialMessages, useStreaming: true,
  });
  const [input, setInput] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streamingText]);

  const handleSend = () => { if (input.trim()) { send(input.trim()); setInput(''); } };
  const handleKey = (e: React.KeyboardEvent) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } };

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0 bg-gray-50 overflow-hidden">
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {messages.length === 0 && !isLoading && (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <div className="w-16 h-16 rounded-2xl bg-kcc-bg flex items-center justify-center mb-4">
              <span className="text-kcc text-2xl font-bold">K</span>
            </div>
            <p className="text-sm">Ask a question about student academic data</p>
          </div>
        )}
        {messages.map(msg => (
          <MessageBubble key={msg.id} message={msg} sessionId={sessionId} />
        ))}
        {isLoading && streamingText && (
          <div className="flex gap-3 mb-4">
            <div className="w-8 h-7 rounded-md flex-shrink-0 shadow-sm bg-kcc-bg flex items-center justify-center">
              <span className="text-kcc text-xs font-bold">K</span>
            </div>
            <div className="max-w-[75%]">
              {currentRoute && <div className="mb-1"><PipelineIndicator route={currentRoute} /></div>}
              <div className="px-4 py-3 bg-white border border-gray-200 rounded-2xl rounded-tl-sm text-sm leading-relaxed">
                {streamingText}<span className="inline-block w-2 h-4 bg-kcc animate-pulse ml-0.5" />
              </div>
            </div>
          </div>
        )}
        {isLoading && !streamingText && (
          <div className="flex gap-3 mb-4">
            <div className="w-8 h-7 rounded-md bg-kcc-bg flex items-center justify-center">
              <span className="text-kcc text-xs font-bold">K</span>
            </div>
            <div className="px-4 py-3 bg-white border border-gray-200 rounded-2xl rounded-tl-sm">
              <div className="flex items-center gap-2 text-sm text-gray-400">
                <Loader2 size={14} className="animate-spin" />
                <span>{currentRoute || 'Analyzing your question...'}</span>
              </div>
            </div>
          </div>
        )}
        {error && (
          <div className="mb-4 px-4 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-600">{error}</div>
        )}
        <div ref={endRef} />
      </div>
      <div className="px-6 py-3 bg-white border-t border-gray-200">
        <div className="flex gap-2.5 items-end">
          <textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKey}
            placeholder="Ask about student data..." rows={1}
            className="flex-1 px-4 py-2.5 border border-gray-200 rounded-xl text-sm resize-none outline-none bg-gray-50 focus:bg-white focus:border-kcc transition-all min-h-[42px] max-h-[120px]" />
          {isLoading ? (
            <button onClick={stop}
              className="px-5 py-2.5 bg-red-500 text-white rounded-xl text-sm font-semibold hover:bg-red-600 transition-all flex items-center gap-1.5">
              <Square size={14} fill="currentColor" /> Stop
            </button>
          ) : (
            <button onClick={handleSend} disabled={!input.trim()}
              className="px-5 py-2.5 bg-kcc text-white rounded-xl text-sm font-semibold hover:bg-kcc-dark disabled:opacity-40 transition-all flex items-center gap-1.5">
              <Send size={14} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
