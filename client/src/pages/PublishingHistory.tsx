import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getPublishingTasks, retryPublishingTask } from '../api/client';
import { History, RefreshCw, ExternalLink, Filter, AlertCircle, CheckCircle2 } from 'lucide-react';

export const PublishingHistory: React.FC = () => {
  const queryClient = useQueryClient();
  const [platformFilter, setPlatformFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['publishing-history', platformFilter, statusFilter],
    queryFn: () =>
      getPublishingTasks({
        platform: platformFilter || undefined,
        status: statusFilter || undefined,
        limit: 50,
      }),
  });

  const retryMutation = useMutation({
    mutationFn: retryPublishingTask,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['publishing-history'] }),
    onError: (err: any) => alert(`Retry failed: ${err.response?.data?.message || err.message}`),
  });

  const tasks = data?.data || [];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* ─── HEADER ───────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Publishing History & Audit Log</h2>
          <p className="text-sm text-slate-500 mt-1">
            Historical log of all automated publishing jobs, API responses, and permalinks.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* Platform Filter */}
          <select
            value={platformFilter}
            onChange={(e) => setPlatformFilter(e.target.value)}
            className="bg-white border border-slate-200 rounded-lg px-3 py-1.5 shadow-sm text-xs font-medium text-slate-700 outline-none"
          >
            <option value="">All Platforms</option>
            <option value="FACEBOOK">Facebook</option>
            <option value="YOUTUBE">YouTube</option>
          </select>

          {/* Status Filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-white border border-slate-200 rounded-lg px-3 py-1.5 shadow-sm text-xs font-medium text-slate-700 outline-none"
          >
            <option value="">All Statuses</option>
            <option value="PUBLISHED">Published</option>
            <option value="FAILED">Failed</option>
            <option value="PENDING">Pending</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </div>
      </div>

      {/* ─── TABLE ────────────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50/75 border-b border-slate-200 text-slate-500 uppercase text-[11px] font-semibold tracking-wider">
              <tr>
                <th className="px-6 py-3.5">Content Title</th>
                <th className="px-6 py-3.5">Platform & Account</th>
                <th className="px-6 py-3.5">Status</th>
                <th className="px-6 py-3.5">Published Date / Link</th>
                <th className="px-6 py-3.5">Error / Details</th>
                <th className="px-6 py-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {isLoading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-slate-400 text-sm">
                    Loading publishing history...
                  </td>
                </tr>
              ) : tasks.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-16 text-center text-slate-400 text-sm">
                    No publishing records found for the selected filters.
                  </td>
                </tr>
              ) : (
                tasks.map((task: any) => (
                  <tr key={task.id} className="hover:bg-slate-50/50 transition-colors">
                    <td className="px-6 py-4 max-w-xs">
                      <p className="font-semibold text-slate-900 truncate">
                        {task.content?.title || task.content?.caption || 'Untitled Post'}
                      </p>
                      <p className="text-[11px] font-mono text-slate-400 truncate mt-0.5">
                        ID: {task.id.substring(0, 12)}...
                      </p>
                    </td>

                    <td className="px-6 py-4">
                      <div className="flex items-center gap-2">
                        <span
                          className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase ${
                            task.platform === 'FACEBOOK'
                              ? 'bg-blue-50 text-blue-700 border border-blue-200'
                              : 'bg-red-50 text-red-700 border border-red-200'
                          }`}
                        >
                          {task.platform}
                        </span>
                        <span className="text-xs text-slate-700 font-medium">
                          {task.socialAccount?.accountName}
                        </span>
                      </div>
                    </td>

                    <td className="px-6 py-4">
                      <span
                        className={`text-xs font-semibold px-2.5 py-1 rounded-full ${
                          task.status === 'PUBLISHED'
                            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                            : task.status === 'FAILED'
                            ? 'bg-rose-50 text-rose-700 border border-rose-200'
                            : 'bg-amber-50 text-amber-700 border border-amber-200'
                        }`}
                      >
                        {task.status}
                      </span>
                    </td>

                    <td className="px-6 py-4">
                      {task.publishedAt ? (
                        <div>
                          <p className="text-xs text-slate-600">
                            {new Date(task.publishedAt).toLocaleString()}
                          </p>
                          {task.externalPostUrl && (
                            <a
                              href={task.externalPostUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="text-[11px] text-indigo-600 hover:text-indigo-700 font-semibold inline-flex items-center gap-1 mt-0.5"
                            >
                              Open Post <ExternalLink className="w-3 h-3" />
                            </a>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </td>

                    <td className="px-6 py-4 max-w-xs">
                      {task.errorMessage ? (
                        <p className="text-xs text-rose-600 line-clamp-2" title={task.errorMessage}>
                          ⚠️ {task.errorMessage}
                        </p>
                      ) : (
                        <span className="text-xs text-slate-400">Success</span>
                      )}
                      {task.retryCount > 0 && (
                        <p className="text-[10px] text-slate-400 mt-0.5">
                          Retried: {task.retryCount} time(s)
                        </p>
                      )}
                    </td>

                    <td className="px-6 py-4 text-right">
                      {task.status === 'FAILED' && (
                        <button
                          onClick={() => retryMutation.mutate(task.id)}
                          disabled={retryMutation.isPending}
                          className="px-2.5 py-1 rounded bg-indigo-50 hover:bg-indigo-100 text-indigo-600 text-xs font-medium inline-flex items-center gap-1 border border-indigo-200"
                        >
                          <RefreshCw className="w-3 h-3" />
                          Retry
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
