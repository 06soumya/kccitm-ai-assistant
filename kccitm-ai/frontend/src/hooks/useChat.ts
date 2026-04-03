'use client';
import { useState, useCallback, useRef } from 'react';
import { streamChat } from '@/lib/sse';
import { sendMessage } from '@/lib/api';
import type { Message, SSEEvent } from '@/lib/types';

interface UseChatOptions {
  sessionId: string;
  initialMessages?: Message[];
  useStreaming?: boolean;
}

export function useChat({
  sessionId,
  initialMessages = [],
  useStreaming = true,
}: UseChatOptions) {
  const [messages,      setMessages]      = useState<Message[]>(initialMessages);
  const [isLoading,     setIsLoading]     = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const [currentRoute,  setCurrentRoute]  = useState('');
  const [error,         setError]         = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    // Save whatever was streamed so far as a partial message
    setStreamingText(prev => {
      if (prev) {
        const partialMsg: Message = {
          id:         `msg_${Date.now()}`,
          role:       'assistant',
          content:    prev + '\n\n*(stopped by user)*',
          metadata:   { route_used: undefined },
          created_at: new Date().toISOString(),
        };
        setMessages(msgs => [...msgs, partialMsg]);
      }
      return '';
    });
    setCurrentRoute('');
    setIsLoading(false);
  }, []);

  const send = useCallback(async (content: string) => {
    if (!content.trim() || isLoading) return;

    setError(null);
    setIsLoading(true);
    setStreamingText('');
    setCurrentRoute('');

    const userMsg: Message = {
      id:         `temp_${Date.now()}`,
      role:       'user',
      content,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, userMsg]);

    if (useStreaming) {
      const controller = new AbortController();
      abortRef.current = controller;

      let accumulated = '';
      let routeUsed   = '';

      await streamChat(
        content,
        sessionId,
        (event: SSEEvent) => {
          if (event.type === 'status') {
            setCurrentRoute(event.message || 'Thinking...');
          } else if (event.type === 'token') {
            accumulated += event.content ?? '';
            setStreamingText(accumulated);
          } else if (event.type === 'done') {
            routeUsed = event.route_used ?? routeUsed;
            const assistantMsg: Message = {
              id:         `msg_${Date.now()}`,
              role:       'assistant',
              content:    accumulated,
              metadata:   {
                total_time_ms: event.total_time_ms,
                route_used:    routeUsed || undefined,
                ...(event.chart_data ? { chart_data: event.chart_data } : {}),
              },
              created_at: new Date().toISOString(),
            };
            setMessages(prev => [...prev, assistantMsg]);
            setStreamingText('');
            setCurrentRoute('');
            setIsLoading(false);
            abortRef.current = null;
          } else if (event.type === 'error') {
            setError(event.message || 'An error occurred');
            setIsLoading(false);
            abortRef.current = null;
          }
        },
        (err) => { setError(err.message); setIsLoading(false); abortRef.current = null; },
        ()    => { setIsLoading(false); abortRef.current = null; },
        controller.signal,
      );
    } else {
      try {
        const res = await sendMessage(content, sessionId);
        const assistantMsg: Message = {
          id:         `msg_${Date.now()}`,
          role:       'assistant',
          content:    res.response,
          metadata:   {
            route_used:    res.route_used,
            total_time_ms: res.total_time_ms,
            ...res.metadata,
          },
          created_at: new Date().toISOString(),
        };
        setMessages(prev => [...prev, assistantMsg]);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setIsLoading(false);
      }
    }
  }, [sessionId, isLoading, useStreaming]);

  return {
    messages, setMessages,
    isLoading, streamingText, currentRoute,
    error, send, stop,
  };
}
