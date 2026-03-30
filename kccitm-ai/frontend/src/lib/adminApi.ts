import { getToken } from './auth';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function adminFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
    ...(options.headers as Record<string, string> || {}),
  };

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Error ${res.status}`);
  }
  return res.json();
}

// Dashboard
export const getDashboardMetrics = () => adminFetch<any>('/api/admin/dashboard/metrics');
export const getQualityStats = () => adminFetch<any>('/api/admin/dashboard/quality');

// Feedback
export const getFeedbackQueue = (limit = 50) => adminFetch<any>(`/api/admin/dashboard/feedback?limit=${limit}`);
export const getImplicitSignals = (limit = 50) => adminFetch<any>(`/api/admin/dashboard/signals?limit=${limit}`);

// Healing
export const getHealingQueue = (status?: string) => adminFetch<any>(`/api/admin/dashboard/healing${status ? `?status=${status}` : ''}`);
export const approveHealingFix = (id: string) => adminFetch<any>(`/api/admin/dashboard/healing/${id}/approve`, { method: 'POST' });
export const rejectHealingFix = (id: string) => adminFetch<any>(`/api/admin/dashboard/healing/${id}/reject`, { method: 'POST' });

// Prompts
export const getPrompts = () => adminFetch<any>('/api/admin/prompts');
export const getPromptProposals = () => adminFetch<any>('/api/admin/prompts/proposals');
export const approvePromptProposal = (id: string) => adminFetch<any>(`/api/admin/prompts/proposals/${id}/approve`, { method: 'POST' });
export const rollbackPrompt = (name: string, section: string) => adminFetch<any>(`/api/admin/prompts/${name}/${section}/rollback`, { method: 'POST' });

// FAQs
export const getFAQs = () => adminFetch<any>('/api/admin/faqs');
export const updateFAQ = (id: string, data: { question: string; answer: string }) => adminFetch<any>(`/api/admin/faqs/${id}`, { method: 'PUT', body: JSON.stringify(data) });
export const retireFAQ = (id: string) => adminFetch<any>(`/api/admin/faqs/${id}`, { method: 'DELETE' });

// Chunks
export const getChunkHealth = () => adminFetch<any>('/api/admin/chunks/health');

// Training
export const getTrainingStats = () => adminFetch<any>('/api/admin/training/stats');
export const getTrainingCandidates = (category?: string) => adminFetch<any>(`/api/admin/training/candidates${category ? `?category=${category}` : ''}`);
export const exportTrainingData = () => adminFetch<any>('/api/admin/training/export', { method: 'POST' });

// Models
export const getModels = () => adminFetch<any>('/api/admin/models');
export const switchModel = (modelName: string) => adminFetch<any>('/api/admin/models/switch', { method: 'POST', body: JSON.stringify({ model_name: modelName }) });

// Cache
export const getCacheStats = () => adminFetch<any>('/api/admin/cache/stats');
export const clearCache = () => adminFetch<any>('/api/admin/cache/clear', { method: 'POST' });

// Health
export const getSystemHealth = () => adminFetch<any>('/api/health');

// Schema
export const refreshSchema = () => adminFetch<any>('/api/admin/dashboard/refresh-schema', { method: 'POST' });

// Jobs
export const triggerHealing = () => adminFetch<any>('/api/admin/jobs/healing/run', { method: 'POST' });
export const triggerFAQGen = () => adminFetch<any>('/api/admin/jobs/faq/run', { method: 'POST' });
export const triggerPromptEvo = () => adminFetch<any>('/api/admin/jobs/prompts/run', { method: 'POST' });
