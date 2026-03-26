'use client';
import { useState } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { useRouter } from 'next/navigation';

export default function LoginForm() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(username, password);
      router.push('/chat');
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally { setLoading(false); }
  };

  return (
    <form onSubmit={handleSubmit} className="bg-white rounded-3xl p-10 w-[420px] shadow-lg border border-gray-200">
      <div className="flex justify-center mb-5">
        <div className="w-24 h-24 rounded-2xl bg-kcc flex items-center justify-center shadow-md">
          <span className="text-white text-3xl font-bold">K</span>
        </div>
      </div>
      <h1 className="text-kcc text-sm font-bold text-center uppercase tracking-wider mb-0.5">
        KCC Institute of Technology &amp; Management
      </h1>
      <h2 className="text-base font-semibold text-center mb-1">AI Academic Assistant</h2>
      <p className="text-sm text-gray-500 text-center mb-7">Sign in to query student data with AI</p>

      {error && <div className="mb-4 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-600">{error}</div>}

      <label className="block text-xs font-medium text-gray-500 mb-1">Username</label>
      <input type="text" value={username} onChange={e => setUsername(e.target.value)}
        className="w-full px-3.5 py-2.5 border border-gray-200 rounded-lg text-sm bg-gray-50 outline-none focus:border-kcc focus:bg-white transition-all mb-4" />

      <label className="block text-xs font-medium text-gray-500 mb-1">Password</label>
      <input type="password" value={password} onChange={e => setPassword(e.target.value)}
        className="w-full px-3.5 py-2.5 border border-gray-200 rounded-lg text-sm bg-gray-50 outline-none focus:border-kcc focus:bg-white transition-all mb-6" />

      <button type="submit" disabled={loading}
        className="w-full py-3 bg-kcc text-white rounded-lg text-sm font-semibold hover:bg-kcc-dark disabled:opacity-50 transition-all">
        {loading ? 'Signing in...' : 'Sign in'}
      </button>
      <p className="text-xs text-gray-400 text-center mt-5">Faculty &amp; admin access only</p>
    </form>
  );
}
