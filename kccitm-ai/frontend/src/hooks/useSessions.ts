'use client';
import { useState, useEffect, useCallback } from 'react';
import { getSessions, createSession, deleteSession } from '@/lib/api';
import type { Session } from '@/lib/types';

export function useSessions() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading]   = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await getSessions();
      setSessions(data.sessions);
    } catch (err) {
      console.error('Failed to load sessions:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const create = useCallback(async (): Promise<string> => {
    const data = await createSession();
    await refresh();
    return data.session_id;
  }, [refresh]);

  const remove = useCallback(async (id: string) => {
    await deleteSession(id);
    await refresh();
  }, [refresh]);

  return { sessions, loading, refresh, create, remove };
}
