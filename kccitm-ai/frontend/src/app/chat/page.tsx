'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { isAuthenticated } from '@/lib/auth';
import { useAuth } from '@/hooks/useAuth';
import { useSessions } from '@/hooks/useSessions';
import { getSession } from '@/lib/api';
import SessionSidebar from '@/components/SessionSidebar';
import ChatWindow from '@/components/ChatWindow';
import type { Message } from '@/lib/types';
import Link from 'next/link';
import { LayoutDashboard, LogOut } from 'lucide-react';

export default function ChatPage() {
  const router = useRouter();
  const { user, logout } = useAuth();
  const { sessions, loading: sessionsLoading, create, refresh } = useSessions();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [initMsgs, setInitMsgs] = useState<Message[]>([]);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => { if (!isAuthenticated()) router.push('/'); }, [router]);

  const handleSelect = async (id: string) => {
    setActiveId(id);
    try {
      const data = await getSession(id);
      setInitMsgs(data.messages || []);
    } catch { setInitMsgs([]); }
  };

  const handleNew = async () => {
    const newId = await create();
    setActiveId(newId);
    setInitMsgs([]);
  };

  useEffect(() => {
    if (initialized || sessionsLoading) return;
    if (sessions.length === 0) {
      // No sessions yet — create one automatically
      setInitialized(true);
      handleNew();
      return;
    }
    setInitialized(true);
    handleSelect(sessions[0].id);
  }, [sessions, sessionsLoading, initialized]);

  return (
    <div className="h-screen flex overflow-hidden">
      <SessionSidebar
        activeId={activeId}
        onSelect={handleSelect}
        onNew={handleNew}
        sessions={sessions}
        loading={sessionsLoading}
        onRefresh={refresh}
      />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <div className="px-5 py-3 bg-white border-b border-gray-200 flex items-center justify-between">
          <h1 className="text-sm font-semibold truncate">
            {sessions.find(s => s.id === activeId)?.title || 'New chat'}
          </h1>
          <div className="flex items-center gap-3">
            {user?.role === 'admin' && (
              <Link href="/admin" className="flex items-center gap-1 text-[11px] px-3 py-1.5 bg-kcc-bg text-kcc rounded-full font-semibold border border-kcc-bg2 hover:bg-kcc-bg2 transition-all">
                <LayoutDashboard size={12} /> Admin
              </Link>
            )}
            <button onClick={logout} className="text-gray-400 hover:text-gray-600 transition-colors">
              <LogOut size={16} />
            </button>
          </div>
        </div>
        {activeId ? (
          <ChatWindow key={activeId} sessionId={activeId} initialMessages={initMsgs} />
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
            <button onClick={handleNew} className="px-4 py-2 bg-kcc text-white rounded-lg hover:bg-kcc-dark text-sm">
              Start a new conversation
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
