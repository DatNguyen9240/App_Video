import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { getPublishingTasks } from '../api/client';
import { Calendar as CalendarIcon, Clock, ChevronLeft, ChevronRight } from 'lucide-react';
import { NavLink } from 'react-router-dom';

export const PublishingCalendar: React.FC = () => {
  const { data } = useQuery({
    queryKey: ['publishing-tasks-calendar'],
    queryFn: () => getPublishingTasks({ limit: 50 }),
  });

  const tasks = data?.data || [];
  const scheduledTasks = tasks.filter((t: any) => t.scheduledAt);

  // Group by date (YYYY-MM-DD)
  const grouped: { [key: string]: any[] } = {};
  scheduledTasks.forEach((t: any) => {
    const key = new Date(t.scheduledAt).toISOString().split('T')[0];
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(t);
  });

  // Simple current month grid representation
  const today = new Date();
  const currentMonthYear = today.toLocaleString('default', { month: 'long', year: 'numeric' });

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* ─── HEADER ───────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Publishing Calendar</h2>
          <p className="text-sm text-slate-500 mt-1">
            Visual calendar timeline of scheduled Facebook and YouTube campaigns.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-slate-700 shadow-sm">
            <CalendarIcon className="w-4 h-4 text-indigo-600" />
            <span>{currentMonthYear}</span>
          </div>
          <NavLink
            to="/create"
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition-all"
          >
            Schedule Post
          </NavLink>
        </div>
      </div>

      {/* ─── CALENDAR TIMELINE VIEW ───────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6 space-y-6">
        <h3 className="font-semibold text-slate-800 text-base flex items-center gap-2">
          <Clock className="w-4 h-4 text-indigo-600" />
          Scheduled Queue Timeline
        </h3>

        {Object.keys(grouped).length === 0 ? (
          <div className="py-16 text-center text-slate-400 text-sm">
            No scheduled publications found. Use the Create Post page to schedule campaigns for future dates.
          </div>
        ) : (
          <div className="space-y-6">
            {Object.keys(grouped)
              .sort()
              .map((dateKey) => (
                <div key={dateKey} className="border-l-2 border-indigo-500 pl-4 space-y-2">
                  <div className="text-xs font-bold text-indigo-600 uppercase tracking-wider">
                    {new Date(dateKey).toLocaleDateString(undefined, {
                      weekday: 'long',
                      month: 'short',
                      day: 'numeric',
                    })}
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {grouped[dateKey].map((task: any) => (
                      <div
                        key={task.id}
                        className="bg-slate-50 border border-slate-200/80 rounded-lg p-3 hover:bg-indigo-50/20 transition-all text-xs"
                      >
                        <div className="flex items-center justify-between mb-1.5">
                          <span
                            className={`font-bold px-2 py-0.5 rounded text-[10px] ${
                              task.platform === 'FACEBOOK'
                                ? 'bg-blue-100 text-blue-700'
                                : 'bg-red-100 text-red-700'
                            }`}
                          >
                            {task.platform}
                          </span>
                          <span className="text-slate-500 font-medium">
                            {new Date(task.scheduledAt).toLocaleTimeString([], {
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </span>
                        </div>
                        <p className="font-semibold text-slate-800 truncate">
                          {task.content?.title || task.content?.caption || 'Untitled Post'}
                        </p>
                        <p className="text-[11px] text-slate-400 mt-1">
                          Account: {task.socialAccount?.accountName}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
};
