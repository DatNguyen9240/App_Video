import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { getOverview, getRecentActivity, getUpcomingQueue } from '../api/client';
import {
  Share2,
  CheckCircle2,
  Clock,
  AlertTriangle,
  FolderOpen,
  Image,
  ExternalLink,
  ArrowUpRight,
} from 'lucide-react';
import { NavLink } from 'react-router-dom';

export const Dashboard: React.FC = () => {
  const { data: overview, isLoading: loadingOverview } = useQuery({
    queryKey: ['dashboard-overview'],
    queryFn: getOverview,
  });

  const { data: recentActivity } = useQuery({
    queryKey: ['dashboard-recent-activity'],
    queryFn: () => getRecentActivity(6),
  });

  const { data: upcomingQueue } = useQuery({
    queryKey: ['dashboard-upcoming-queue'],
    queryFn: () => getUpcomingQueue(5),
  });

  const stats = [
    {
      title: 'Connected Accounts',
      value: overview?.connectedAccounts?.total ?? 0,
      sub: `${overview?.connectedAccounts?.facebook ?? 0} FB, ${overview?.connectedAccounts?.youtube ?? 0} YT`,
      icon: Share2,
      color: 'text-indigo-600 bg-indigo-50 border-indigo-200/60',
    },
    {
      title: 'Published Posts',
      value: overview?.publishing?.totalPublished ?? 0,
      sub: 'All time via Official APIs',
      icon: CheckCircle2,
      color: 'text-emerald-600 bg-emerald-50 border-emerald-200/60',
    },
    {
      title: 'Scheduled in Queue',
      value: overview?.publishing?.scheduled ?? 0,
      sub: 'Survives server restarts',
      icon: Clock,
      color: 'text-amber-600 bg-amber-50 border-amber-200/60',
    },
    {
      title: 'Failed (Needs Retry)',
      value: overview?.publishing?.failed ?? 0,
      sub: 'Independent retry available',
      icon: AlertTriangle,
      color: 'text-rose-600 bg-rose-50 border-rose-200/60',
    },
  ];

  return (
    <div className="space-y-8 max-w-7xl mx-auto">
      {/* ─── HEADER ───────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Publisher Dashboard</h2>
          <p className="text-sm text-slate-500 mt-1">
            Real-time status of your connected Facebook Pages and YouTube Channels.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <NavLink
            to="/accounts"
            className="px-4 py-2 rounded-lg border border-slate-200 bg-white text-slate-700 text-xs font-semibold hover:bg-slate-50 transition-all shadow-sm"
          >
            Manage Accounts
          </NavLink>
          <NavLink
            to="/create"
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition-all shadow-indigo-500/20"
          >
            Create New Campaign
          </NavLink>
        </div>
      </div>

      {/* ─── METRIC CARDS ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        {stats.map((s) => {
          const Icon = s.icon;
          return (
            <div key={s.title} className="bg-white p-6 rounded-xl border border-slate-200/80 shadow-sm">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{s.title}</span>
                <div className={`w-9 h-9 rounded-lg border flex items-center justify-center ${s.color}`}>
                  <Icon className="w-4 h-4" />
                </div>
              </div>
              <div className="mt-4">
                <span className="text-3xl font-extrabold text-slate-900 tracking-tight">
                  {loadingOverview ? '...' : s.value}
                </span>
                <p className="text-xs text-slate-400 mt-1">{s.sub}</p>
              </div>
            </div>
          );
        })}
      </div>

      {/* ─── TWO COLUMN ACTIVITY & QUEUE ──────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Recent Activity */}
        <div className="lg:col-span-2 bg-white rounded-xl border border-slate-200/80 shadow-sm p-6">
          <div className="flex items-center justify-between pb-4 border-b border-slate-100">
            <div>
              <h3 className="font-semibold text-slate-900 text-base">Recent Publishing Activity</h3>
              <p className="text-xs text-slate-500">Live API execution results</p>
            </div>
            <NavLink to="/history" className="text-xs font-medium text-indigo-600 hover:text-indigo-700 flex items-center gap-1">
              View all <ArrowUpRight className="w-3.5 h-3.5" />
            </NavLink>
          </div>

          <div className="divide-y divide-slate-100 mt-2">
            {!recentActivity || recentActivity.length === 0 ? (
              <div className="py-12 text-center text-slate-400 text-sm">
                No recent publishing activities found. Create your first post to begin!
              </div>
            ) : (
              recentActivity.map((task: any) => (
                <div key={task.id} className="py-3.5 flex items-center justify-between gap-4">
                  <div className="flex items-center gap-3 min-w-0">
                    <span
                      className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold shrink-0 ${
                        task.platform === 'FACEBOOK'
                          ? 'bg-blue-50 text-blue-600 border border-blue-200'
                          : 'bg-red-50 text-red-600 border border-red-200'
                      }`}
                    >
                      {task.platform === 'FACEBOOK' ? 'FB' : 'YT'}
                    </span>
                    <div className="truncate">
                      <p className="text-xs font-semibold text-slate-900 truncate">
                        {task.content?.title || task.content?.caption || 'Untitled Post'}
                      </p>
                      <p className="text-[11px] text-slate-500">
                        {task.socialAccount?.accountName || task.platform} •{' '}
                        {new Date(task.updatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 shrink-0">
                    <span
                      className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                        task.status === 'PUBLISHED'
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                          : task.status === 'FAILED'
                          ? 'bg-rose-50 text-rose-700 border border-rose-200'
                          : 'bg-amber-50 text-amber-700 border border-amber-200'
                      }`}
                    >
                      {task.status}
                    </span>
                    {task.externalPostUrl && (
                      <a
                        href={task.externalPostUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="text-slate-400 hover:text-indigo-600"
                        title="Open on Platform"
                      >
                        <ExternalLink className="w-4 h-4" />
                      </a>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Upcoming Scheduled Queue */}
        <div className="bg-white rounded-xl border border-slate-200/80 shadow-sm p-6 flex flex-col">
          <div className="flex items-center justify-between pb-4 border-b border-slate-100">
            <div>
              <h3 className="font-semibold text-slate-900 text-base">Upcoming Queue</h3>
              <p className="text-xs text-slate-500">Scheduled BullMQ jobs</p>
            </div>
            <NavLink to="/queue" className="text-xs font-medium text-indigo-600 hover:text-indigo-700 flex items-center gap-1">
              Queue <ArrowUpRight className="w-3.5 h-3.5" />
            </NavLink>
          </div>

          <div className="divide-y divide-slate-100 mt-2 flex-1">
            {!upcomingQueue || upcomingQueue.length === 0 ? (
              <div className="py-12 text-center text-slate-400 text-sm">
                No posts currently scheduled in the queue.
              </div>
            ) : (
              upcomingQueue.map((item: any) => (
                <div key={item.id} className="py-3">
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="font-semibold text-slate-800">{item.platform}</span>
                    <span className="text-[11px] text-amber-600 bg-amber-50 px-2 py-0.5 rounded border border-amber-200/60 font-medium">
                      {item.scheduledAt ? new Date(item.scheduledAt).toLocaleDateString() : 'Pending'}
                    </span>
                  </div>
                  <p className="text-xs text-slate-600 line-clamp-1">
                    {item.content?.title || item.content?.caption || 'Untitled'}
                  </p>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Account: {item.socialAccount?.accountName}
                  </p>
                </div>
              ))
            )}
          </div>

          <div className="pt-4 border-t border-slate-100">
            <NavLink
              to="/calendar"
              className="w-full block text-center py-2 rounded-lg bg-slate-50 hover:bg-slate-100 text-slate-700 text-xs font-semibold border border-slate-200 transition-all"
            >
              Open Publishing Calendar
            </NavLink>
          </div>
        </div>
      </div>
    </div>
  );
};
