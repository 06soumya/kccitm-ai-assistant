'use client';
import { useState, useEffect, useCallback } from 'react';
import {
  getUser, getToken, removeToken,
  setToken, setUser as saveUser,
} from '@/lib/auth';
import { login as apiLogin } from '@/lib/api';
import type { User } from '@/lib/types';

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const stored = getUser();
    const token  = getToken();
    if (stored && token) setUser(stored as User);
    setLoading(false);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await apiLogin(username, password);
    setToken(res.access_token);
    const userData: User = {
      user_id:  res.user_id,
      username: res.username,
      role:     res.role as 'admin' | 'faculty',
    };
    saveUser(userData);
    setUser(userData);
    return userData;
  }, []);

  const logout = useCallback(() => {
    removeToken();
    setUser(null);
    window.location.href = '/';
  }, []);

  return { user, loading, login, logout, isAuthenticated: !!user };
}
