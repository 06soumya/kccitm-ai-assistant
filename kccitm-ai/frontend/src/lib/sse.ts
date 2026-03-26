import { getToken } from './auth';
import type { SSEEvent } from './types';

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function streamChat(
  message: string,
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onComplete: () => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken();

  try {
    const response = await fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ message, session_id: sessionId, stream: true }),
      signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error('No response body');

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('data: ')) continue;
        try {
          const event: SSEEvent = JSON.parse(trimmed.slice(6));
          onEvent(event);
          if (event.type === 'done' || event.type === 'error') {
            onComplete();
            return;
          }
        } catch {
          // skip malformed SSE frames
        }
      }
    }
    onComplete();
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      onComplete();
      return;
    }
    onError(err instanceof Error ? err : new Error(String(err)));
  }
}
