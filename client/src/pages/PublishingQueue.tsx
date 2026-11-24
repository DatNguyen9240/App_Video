import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getPublishingTasks, cancelPublishingTask, reschedulePublishingTask } from '../api/client';
import { Clock, XCircle, Calendar, AlertCircle } from 'lucide-react';

export const PublishingQueue: React.FC = () => {
  const queryClient = useQueryClient();
  const [rescheduleTaskId, setRescheduleTaskId] = useState<string | null>(null);
  const [newDate, setNewDate] = useState<string>('');

  const { data, isLoading } = useQuery({
    queryKey: ['publishing-queue'],
    queryFn: () => getPublishingTasks({ status: 'PENDING', limit: 30 }),
  });

  const cancelMutation = useMutation({
    mutationFn: cancelPublishingTask,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['publishing-queue'] }),
  });

  const rescheduleMutation = useMutation({
    mutationFn: ({ taskId, scheduledAt }: { taskId: string; scheduledAt: string }) =>
      reschedulePublishingTask(taskId, scheduledAt),
    onSuccess: () => {
      setRescheduleTaskId(null);
      queryClient.invalidateQueries({ queryKey: ['publishing-queue'] });
    },
  });

  const tasks = data?.data || [];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* ─── HEADER ───────────────────────────────────────────────────────── */}
      <div>
        <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Publishing Queue</h2>
        <p className="text-sm text-slate-500 mt-1">
          Active background jobs stored in Redis BullMQ awaiting automated execution.
        </p>
      </div>

      {/* ─── QUEUE TABLE ──────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50/75 border-b border-slate-200 text-slate-500 uppercase text-[11px] font-semibold tracking-wider">
              <tr>
                <th className="px-6 py-3.5">Content</th>
                <th className="px-6 py-3.5">Platform & Account</th>
                <th className="px-6 py-3.5">Scheduled Publish Time</th>
                <th className="px-6 py-3.5">Idempotency Key</th>
                <th className="px-6 py-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center text-slate-400 text-sm">
                    Loading publishing queue...
                  </td>
                </tr>
              ) : tasks.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-6 py-16 text-center text-slate-400 text-sm">
                    No tasks currently waiting in the queue.
                  </td>
                </tr>
              ) : (
                tasks.map((task: any) => (
                  <tr key={task.id} className="hover:bg-slate-50/50 transition-colors">
                    <td className="px-6 py-4 max-w-xs truncate font-semibold text-slate-900">
                      {task.content?.title || task.content?.caption || 'Untitled Post'}
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
                        <span className="text-xs text-slate-600">{task.socialAccount?.accountName}</span>
                      </div>
                    </td>

                    <td className="px-6 py-4 text-xs font-medium text-amber-700">
                      📅 {task.scheduledAt ? new Date(task.scheduledAt).toLocaleString() : 'Immediate'}
                    </td>

                    <td className="px-6 py-4 text-[11px] font-mono text-slate-400 truncate max-w-[140px]">
                      {task.idempotencyKey}
                    </td>

                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => setRescheduleTaskId(task.id)}
                          className="px-2.5 py-1 rounded bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-medium"
                        >
                          Reschedule
                        </button>
                        <button
                          onClick={() => {
                            if (confirm('Cancel this queued task?')) {
                              cancelMutation.mutate(task.id);
                            }
                          }}
                          className="px-2.5 py-1 rounded bg-rose-50 hover:bg-rose-100 text-rose-600 text-xs font-medium border border-rose-200"
                        >
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Reschedule Modal */}
      {rescheduleTaskId && (
        <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6 space-y-4">
            <h3 className="font-bold text-slate-900 text-base">Reschedule Publishing Task</h3>
            <p className="text-xs text-slate-500">
              Select a new date and time for the automated background worker to publish this post.
            </p>
            <input
              type="datetime-local"
              value={newDate}
              onChange={(e) => setNewDate(e.target.value)}
              className="w-full px-3.5 py-2 rounded-lg border border-slate-200 text-xs text-slate-800"
            />
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setRescheduleTaskId(null)}
                className="px-4 py-2 rounded-lg border border-slate-200 text-xs text-slate-700"
              >
                Close
              </button>
              <button
                onClick={() => {
                  if (newDate) {
                    rescheduleMutation.mutate({ taskId: rescheduleTaskId, scheduledAt: newDate });
                  }
                }}
                disabled={!newDate || rescheduleMutation.isPending}
                className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold"
              >
                Confirm Reschedule
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
