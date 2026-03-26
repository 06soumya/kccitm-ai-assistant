import Cookies from 'js-cookie';

const TOKEN_KEY = 'kccitm_token';
const USER_KEY  = 'kccitm_user';

export function setToken(token: string): void {
  Cookies.set(TOKEN_KEY, token, { expires: 1 });
}

export function getToken(): string | null {
  return Cookies.get(TOKEN_KEY) ?? null;
}

export function removeToken(): void {
  Cookies.remove(TOKEN_KEY);
  Cookies.remove(USER_KEY);
}

export function setUser(user: { user_id: string; username: string; role: string }): void {
  Cookies.set(USER_KEY, JSON.stringify(user), { expires: 1 });
}

export function getUser(): { user_id: string; username: string; role: string } | null {
  const raw = Cookies.get(USER_KEY);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

export function isAdmin(): boolean {
  return getUser()?.role === 'admin';
}
