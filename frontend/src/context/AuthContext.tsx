import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || '';
const TOKEN_KEY = 'auth_token';

interface User {
  email: string;
  name: string;
  picture: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: () => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  login: () => {},
  logout: async () => {},
});

function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

function storeToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const popupRef = useRef<Window | null>(null);

  const fetchUser = useCallback(() => {
    const token = getStoredToken();
    if (!token) {
      setUser(null);
      return Promise.resolve();
    }
    return fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(res => (res.ok ? res.json() : null))
      .then(data => {
        if (data) {
          setUser(data);
        } else {
          clearToken();
          setUser(null);
        }
      })
      .catch(() => {
        clearToken();
        setUser(null);
      });
  }, []);

  // Check for token in URL fragment (mobile redirect flow)
  useEffect(() => {
    const hash = window.location.hash;
    if (hash.includes('auth_token=')) {
      const token = hash.split('auth_token=')[1]?.split('&')[0];
      if (token) {
        storeToken(token);
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
      }
    }
    // Handle auth errors from backend redirect
    const params = new URLSearchParams(window.location.search);
    const authError = params.get('auth_error');
    if (authError) {
      params.delete('auth_error');
      const cleaned = params.toString();
      window.history.replaceState(null, '', window.location.pathname + (cleaned ? `?${cleaned}` : ''));
    }
  }, []);

  useEffect(() => {
    fetchUser().finally(() => setLoading(false));
  }, [fetchUser]);

  // Listen for popup completion
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      const expectedOrigin = API_BASE ? new URL(API_BASE).origin : window.location.origin;
      if (e.origin !== expectedOrigin) return;
      if (e.data?.type === 'auth_complete' && e.data?.token) {
        storeToken(e.data.token);
        fetchUser();
        popupRef.current = null;
      }
      // Backwards compat
      if (e.data === 'auth_complete') {
        fetchUser();
        popupRef.current = null;
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [fetchUser]);

  const login = useCallback(() => {
    const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
    const returnTo = window.location.pathname + window.location.search;

    if (isMobile) {
      window.location.href = `${API_BASE}/api/auth/google?returnTo=${encodeURIComponent(returnTo)}`;
      return;
    }

    // Desktop: popup flow
    const w = 500;
    const h = 600;
    const left = window.screenX + (window.outerWidth - w) / 2;
    const top = window.screenY + (window.outerHeight - h) / 2;

    const popup = window.open(
      `${API_BASE}/api/auth/google?popup=1`,
      'signin',
      `width=${w},height=${h},left=${left},top=${top},popup=1`,
    );

    if (popup) {
      popupRef.current = popup;
      const interval = setInterval(() => {
        if (popup.closed) {
          clearInterval(interval);
          fetchUser();
          popupRef.current = null;
        }
      }, 500);
    } else {
      window.location.href = `${API_BASE}/api/auth/google?returnTo=${encodeURIComponent(returnTo)}`;
    }
  }, [fetchUser]);

  const logout = useCallback(async () => {
    const token = getStoredToken();
    await fetch(`${API_BASE}/api/auth/logout`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
