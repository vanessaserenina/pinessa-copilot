import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, errorMessage, request } from "./api";
import type { Api, User } from "./api";
import { Catalogue } from "./Catalogue";
import { ProductDetailScreen } from "./ProductDetailScreen";
import "./App.css";

const SESSION_KEY = "pinessa.accessToken";

function Brand() {
  return <div className="brand"><span className="brand-mark" aria-hidden="true">P</span><span>Pinessa<small>Order-to-delivery</small></span></div>;
}

function Login({ notice, onLogin }: { notice: string; onLogin: (token: string, user: User) => void }) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setPending(true);
    setError("");
    try {
      const { access_token } = await request<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: new URLSearchParams({ username: String(fields.get("email")).trim(), password: String(fields.get("password")) }),
      });
      const user = await request<User>("/auth/me", { headers: { Authorization: `Bearer ${access_token}` } });
      onLogin(access_token, user);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="login-shell">
      <aside className="login-brand"><Brand /></aside>
      <main className="login-content">
        <form className="login-form" onSubmit={signIn}>
          <h1>Sign in</h1>
          <p className="muted">Access the product catalogue and component requirements.</p>
          {notice && <p className="notice" role="status">{notice}</p>}
          {error && <p className="error" role="alert">{error}</p>}
          <label>Email<input name="email" type="email" autoComplete="username" required /></label>
          <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
          <button className="primary" disabled={pending}>{pending ? "Signing in…" : "Sign in"}</button>
        </form>
      </main>
    </div>
  );
}

function productFromHash() {
  return window.location.hash.match(/^#\/products\/([1-9]\d*)$/)?.[1] ?? null;
}

function Workspace({ user, api, onSignOut }: { user: User; api: Api; onSignOut: () => void }) {
  const [productId, setProductId] = useState(productFromHash);
  const [catalogueRevision, setCatalogueRevision] = useState(0);

  useEffect(() => {
    function navigate() {
      setProductId(productFromHash());
      window.scrollTo(0, 0);
    }
    window.addEventListener("hashchange", navigate);
    return () => window.removeEventListener("hashchange", navigate);
  }, []);

  return (
    <div className="workspace">
      <aside className="sidebar">
        <Brand />
        <nav aria-label="Main navigation">
          <a href="#/products" aria-current={productId ? undefined : "page"}>
            <span className="catalogue-icon" aria-hidden="true" /> Catalogue
          </a>
        </nav>
      </aside>
      <div className="workspace-body">
        <header className="topbar">
          <span className="topbar-title">Product workspace</span>
          <div className="account">
            <div className="account-name"><strong>{user.full_name}</strong><small>{user.role}</small></div>
            <button className="secondary" onClick={onSignOut}>Sign out</button>
          </div>
        </header>
        <main className="content">
          <div hidden={productId !== null}><Catalogue api={api} revision={catalogueRevision} /></div>
          {productId && <ProductDetailScreen key={productId} id={productId} api={api} isAdmin={user.role === "admin"}
            onBomChange={() => setCatalogueRevision((revision) => revision + 1)} />}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState(() => ({ token: sessionStorage.getItem(SESSION_KEY), user: null as User | null }));
  const [notice, setNotice] = useState("");
  const [restoreError, setRestoreError] = useState("");
  const { token, user } = session;

  const signOut = useCallback((message = "") => {
    sessionStorage.removeItem(SESSION_KEY);
    setSession({ token: null, user: null });
    setNotice(message);
    setRestoreError("");
  }, []);

  const api: Api = useCallback(async <T,>(path: string, options?: RequestInit) => {
    const headers = new Headers(options?.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    try {
      return await request<T>(path, { ...options, headers });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401 && !options?.signal?.aborted && sessionStorage.getItem(SESSION_KEY) === token) {
        signOut("Your session ended. Sign in again to continue.");
      }
      throw error;
    }
  }, [token, signOut]);

  useEffect(() => {
    if (!token || user || restoreError) return;
    const controller = new AbortController();
    request<User>("/auth/me", { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal }).then(
      (restored) => {
        if (!controller.signal.aborted) setSession({ token, user: restored });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) signOut("Your session ended. Sign in again to continue.");
        else setRestoreError(errorMessage(error));
      },
    );
    return () => controller.abort();
  }, [token, user, restoreError, signOut]);

  if (user) return <Workspace user={user} api={api} onSignOut={() => signOut()} />;
  if (token) {
    return <main className="session-status">
      {restoreError ? <><p className="error" role="alert">{restoreError}</p><div className="actions">
        <button className="primary" onClick={() => setRestoreError("")}>Retry</button>
        <button className="secondary" onClick={() => signOut()}>Sign out</button>
      </div></> : <p role="status">Restoring your session…</p>}
    </main>;
  }
  return <Login notice={notice} onLogin={(token, user) => {
    sessionStorage.setItem(SESSION_KEY, token);
    setSession({ token, user });
  }} />;
}
