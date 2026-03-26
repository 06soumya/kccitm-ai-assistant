'use client';
import { useState } from 'react';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
import { submitFeedback } from '@/lib/api';

export default function FeedbackButtons({ messageId, sessionId }: { messageId: string; sessionId: string }) {
  const [selected, setSelected] = useState<'up' | 'down' | null>(null);
  const [showText, setShowText] = useState(false);
  const [text, setText] = useState('');

  const handle = async (rating: number) => {
    const dir = rating === 5 ? 'up' : 'down';
    setSelected(dir);
    if (rating === 1) { setShowText(true); return; }
    try { await submitFeedback({ message_id: messageId, session_id: sessionId, rating }); } catch {}
  };

  const submitText = async () => {
    try {
      await submitFeedback({ message_id: messageId, session_id: sessionId, rating: 1, feedback_text: text });
      setShowText(false);
    } catch {}
  };

  return (
    <div className="mt-2 pt-2 border-t border-gray-100">
      <div className="flex gap-1">
        <button onClick={() => handle(5)} className={`p-1 rounded transition-all hover:bg-gray-50 ${selected === 'up' ? 'opacity-100' : 'opacity-30 hover:opacity-70'}`}>
          <ThumbsUp size={14} />
        </button>
        <button onClick={() => handle(1)} className={`p-1 rounded transition-all hover:bg-gray-50 ${selected === 'down' ? 'opacity-100' : 'opacity-30 hover:opacity-70'}`}>
          <ThumbsDown size={14} />
        </button>
      </div>
      {showText && (
        <div className="mt-2 flex gap-2">
          <input value={text} onChange={e => setText(e.target.value)} placeholder="What went wrong?" className="flex-1 text-xs px-2 py-1 border border-gray-200 rounded-md outline-none focus:border-kcc" />
          <button onClick={submitText} className="text-xs px-3 py-1 bg-kcc text-white rounded-md font-medium">Send</button>
        </div>
      )}
    </div>
  );
}
