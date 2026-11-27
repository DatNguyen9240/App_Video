import React from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  PlusCircle,
  FolderOpen,
  Share2,
  Image,
  Calendar,
  ListOrdered,
  History,
  Settings,
  Sparkles,
  ExternalLink,
  LogIn,
  LogOut,
} from 'lucide-react';

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Create Post', href: '/create', icon: PlusCircle },
  { name: 'Content Library', href: '/content', icon: FolderOpen },
  { name: 'Connected Accounts', href: '/accounts', icon: Share2 },
  { name: 'Media Library', href: '/media', icon: Image },
  { name: 'Publishing Calendar', href: '/calendar', icon: Calendar },
  { name: 'Publishing Queue', href: '/queue', icon: ListOrdered },
  { name: 'Publishing History', href: '/history', icon: History },
  { name: 'Settings', href: '/settings', icon: Settings },
];

export const Layout: React.FC = () => {
  const location = useLocation();
  const storedUserRaw = typeof window !== 'undefined' ? localStorage.getItem('autopub_user') : null;
  let currentUser: { email?: string; name?: string } | null = null;
  try {
    currentUser = storedUserRaw ? JSON.parse(storedUserRaw) : null;
  } catch {
    currentUser = null;
  }

  const handleLogout = () => {
    localStorage.removeItem('autopub_token');
    localStorage.removeItem('autopub_user');
    window.location.href = '/login';
  };

  return (
    <div className="flex h-screen bg-slate-50 overflow-hidden">
      {/* ─── SIDEBAR ──────────────────────────────────────────────────────── */}
      <aside className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col z-20 shrink-0">
        <div className="h-16 flex items-center px-6 gap-3 border-b border-slate-800 bg-slate-950/50">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-500 to-purple-500 flex items-center justify-center text-white shadow-lg shadow-indigo-500/30">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <h1 className="font-bold text-base text-white tracking-tight flex items-center gap-1.5">
              AutoPub <span className="text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">PRO</span>
            </h1>
            <p className="text-[11px] text-slate-400">Meta & YouTube Publishing</p>
          </div>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {navigation.map((item) => {
            const Icon = item.icon;
            const isActive = location.pathname === item.href;
            return (
              <NavLink
                key={item.name}
                to={item.href}
                className={`flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  isActive
                    ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/30'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                }`}
              >
                <Icon className={`w-4 h-4 ${isActive ? 'text-white' : 'text-slate-400'}`} />
                {item.name}
              </NavLink>
            );
          })}
        </nav>

        <div className="p-4 border-t border-slate-800 bg-slate-950/40 space-y-3">
          <div className="bg-slate-800/60 rounded-lg p-3 text-xs text-slate-400 border border-slate-700/50">
            <div className="flex items-center justify-between text-slate-300 font-semibold mb-1">
              <span>{currentUser ? (currentUser.name || currentUser.email) : 'Demo Workspace'}</span>
              <span className="w-2 h-2 rounded-full bg-emerald-400 ring-4 ring-emerald-400/20"></span>
            </div>
            <p className="text-[11px] text-slate-500 mb-2">
              {currentUser ? currentUser.email : 'Multi-user JWT + DB Config'}
            </p>
            <div className="flex items-center justify-between">
              <a
                href="http://localhost:3000/api/docs"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-indigo-400 hover:text-indigo-300 text-[11px] font-medium"
              >
                Swagger <ExternalLink className="w-3 h-3" />
              </a>
              {currentUser ? (
                <button
                  type="button"
                  onClick={handleLogout}
                  className="inline-flex items-center gap-1 text-rose-400 hover:text-rose-300 text-[11px] font-medium"
                >
                  <LogOut className="w-3 h-3" /> Logout
                </button>
              ) : (
                <NavLink
                  to="/login"
                  className="inline-flex items-center gap-1 text-emerald-400 hover:text-emerald-300 text-[11px] font-medium"
                >
                  <LogIn className="w-3 h-3" /> Sign In
                </NavLink>
              )}
            </div>
          </div>
        </div>
      </aside>

      {/* ─── MAIN CONTENT ─────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="h-16 bg-white border-b border-slate-200 px-8 flex items-center justify-between shrink-0 shadow-sm">
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200/60 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
              Live Official APIs
            </span>
            <span className="text-xs font-medium text-slate-400 hidden sm:inline">•</span>
            <span className="text-xs text-slate-500 hidden sm:inline">Idempotent Multi-Platform Auto Publisher</span>
          </div>

          <div className="flex items-center gap-3">
            <NavLink
              to="/create"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition-all shadow-indigo-500/20"
            >
              <PlusCircle className="w-3.5 h-3.5" />
              New Post
            </NavLink>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
};
