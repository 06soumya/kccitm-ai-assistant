import { getToken, removeToken } from './auth';
import type {
  LoginResponse, ChatResponse, Session,
  Message, HealthCheck, FeedbackRequest,
} from './types';

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function fetchAPI<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> ?? {}),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    removeToken();
    window.location.href = '/';
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API error ${res.status}`);
  }
  return res.json();
}

// Auth
export async function login(username: string, password: string): Promise<LoginResponse> {
  return fetchAPI('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

// Chat
export async function sendMessage(message: string, sessionId?: string): Promise<ChatResponse> {
  return fetchAPI('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ message, session_id: sessionId, stream: false }),
  });
}

// Sessions
export async function getSessions(): Promise<{ sessions: Session[] }> {
  return fetchAPI('/api/sessions');
}

export async function getSession(id: string): Promise<{ id: string; title: string; messages: Message[] }> {
  return fetchAPI(`/api/sessions/${id}`);
}

export async function createSession(): Promise<{ session_id: string }> {
  return fetchAPI('/api/sessions', { method: 'POST' });
}

export async function deleteSession(id: string): Promise<void> {
  await fetchAPI(`/api/sessions/${id}`, { method: 'DELETE' });
}

// Feedback
export async function submitFeedback(feedback: FeedbackRequest): Promise<unknown> {
  return fetchAPI('/api/feedback', {
    method: 'POST',
    body: JSON.stringify(feedback),
  });
}

// Health
export async function getHealth(): Promise<HealthCheck> {
  return fetchAPI('/api/health');
}
