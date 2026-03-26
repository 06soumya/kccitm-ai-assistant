'use client';
import { useState } from 'react';
import { Plus, Trash2, MessageSquare } from 'lucide-react';
import { deleteSession } from '@/lib/api';
import type { Session } from '@/lib/types';

interface Props {
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew:    () => void;
  sessions: Session[];
  loading?: boolean;
  onRefresh?: () => void;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffH = (now.getTime() - d.getTime()) / 3600000;
  if (diffH < 24) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (diffH < 168) return d.toLocaleDateString([], { weekday: 'short' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export default function SessionSidebar({ activeId, onSelect, onNew, sessions, loading, onRefresh }: Props) {
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!confirm('Delete this chat?')) return;
    setDeletingId(id);
    try {
      await deleteSession(id);
      onRefresh?.();
    } finally { setDeletingId(null); }
  };

  return (
    <div className="w-64 h-full bg-white border-r border-gray-200 flex flex-col flex-shrink-0 overflow-hidden">
      <div className="p-3 border-b border-gray-200">
        <button onClick={onNew}
          className="w-full flex items-center justify-center gap-2 px-3 py-2.5 bg-kcc hover:bg-kcc-dark text-white text-xs font-semibold rounded-lg transition-colors">
          <Plus size={14} /> New chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="text-center text-xs text-gray-400 py-6">Loading...</div>
        ) : sessions.length === 0 ? (
          <div className="text-center text-xs text-gray-400 py-6">No conversations yet</div>
        ) : sessions.map((session: Session) => (
          <div
            key={session.id}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(session.id)}
            onKeyDown={e => { if (e.key === 'Enter') onSelect(session.id); }}
            className={`
              w-full group flex items-start gap-2 px-3 py-2.5 rounded-lg mb-0.5 cursor-pointer text-left transition-colors
              ${activeId === session.id
                ? 'bg-kcc-bg text-kcc-text border border-kcc-bg2'
                : 'hover:bg-gray-50 text-gray-600 border border-transparent'
              }
            `}
          >
            <MessageSquare size={13} className={`mt-0.5 shrink-0 ${activeId === session.id ? 'text-kcc' : 'text-gray-400'}`} />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium truncate">{session.title || 'New chat'}</p>
              <p className="text-[10px] text-gray-400 mt-0.5">
                {formatDate(session.updated_at || session.created_at)}
                {session.message_count != null && <span className="ml-1">· {session.message_count} msgs</span>}
              </p>
            </div>
            <button
              onClick={e => handleDelete(e, session.id)}
              disabled={deletingId === session.id}
              className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition-all shrink-0"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
