export interface User {
  user_id: string;
  username: string;
  role: 'admin' | 'faculty';
}

export interface LoginResponse {
  access_token: string;
  user_id: string;
  username: string;
  role: string;
}

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
}

export interface MessageMetadata {
  route_used?: string;
  total_time_ms?: number;
  cache_hit?: boolean;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  metadata?: MessageMetadata;
  created_at: string;
}

export interface ChatResponse {
  response: string;
  session_id: string;
  route_used: string;
  total_time_ms: number;
  metadata: Record<string, unknown>;
}

export interface SSEEvent {
  type: 'status' | 'token' | 'done' | 'error';
  message?: string;
  content?: string;
  route_used?: string;
  total_time_ms?: number;
  session_id?: string;
  chart_data?: Record<string, unknown>;
}

export interface FeedbackRequest {
  message_id: string;
  session_id: string;
  rating: number;
  feedback_text?: string;
}

export interface HealthCheck {
  status: string;
  services: Record<string, { status: string; message?: string }>;
}
