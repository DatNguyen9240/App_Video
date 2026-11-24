import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getContents, deleteContent, publishNow } from '../api/client';
import { PlusCircle, Trash2, Send, ExternalLink, Filter } from 'lucide-react';
import { NavLink } from 'react-router-dom';

export const ContentLibrary: React.FC = () => {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>('');

  const { data, isLoading } = useQuery({
    queryKey: ['contents', statusFilter],
    queryFn: () => getContents({ status: statusFilter || undefined }),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteContent,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['contents'] }),
  });

  const publishMutation = useMutation({
    mutationFn: publishNow,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['contents'] }),
  });

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* ─── HEADER ───────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Content Library</h2>
          <p className="text-sm text-slate-500 mt-1">
            Manage your post drafts, campaigns, and scheduled publications.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-white border border-slate-200 rounded-lg px-3 py-1.5 shadow-sm text-xs">
            <Filter className="w-3.5 h-3.5 text-slate-400" />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="bg-transparent border-none text-slate-700 font-medium focus:ring-0 outline-none cursor-pointer"
            >
              <option value="">All Statuses</option>
              <option value="DRAFT">Drafts</option>
              <option value="SCHEDULED">Scheduled</option>
              <option value="PUBLISHED">Published</option>
              <option value="PARTIALLY_PUBLISHED">Partially Published</option>
              <option value="FAILED">Failed</option>
            </select>
          </div>

          <NavLink
            to="/create"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition-all"
          >
            <PlusCircle className="w-3.5 h-3.5" />
            Create Post
          </NavLink>
        </div>
      </div>

      {/* ─── POSTS TABLE ──────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200/80 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50/75 border-b border-slate-200 text-slate-500 uppercase text-[11px] font-semibold tracking-wider">
              <tr>
                <th className="px-6 py-3.5">Post Content</th>
                <th className="px-6 py-3.5">Target Platforms</th>
                <th className="px-6 py-3.5">Status</th>
                <th className="px-6 py-3.5">Schedule / Date</th>
                <th className="px-6 py-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-6 py-12 text-center text-slate-400 text-sm">
                    Loading content posts...
                  </td>
                </tr>
              ) : !data?.data || data.data.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-6 py-16 text-center text-slate-400 text-sm">
                    No content posts match your filters.
                  </td>
                </tr>
              ) : (
                data.data.map((post: any) => (
                  <tr key={post.id} className="hover:bg-slate-50/50 transition-colors">
                    <td className="px-6 py-4 max-w-sm">
                      <p className="font-semibold text-slate-900 truncate">
                        {post.title || post.caption || 'Untitled Post'}
                      </p>
                      <p className="text-xs text-slate-500 line-clamp-1 mt-0.5">
                        {post.caption || post.description || 'No caption'}
                      </p>
                      {post.mediaItems?.length > 0 && (
                        <span className="text-[10px] text-indigo-600 font-medium">
                          📎 {post.mediaItems.length} media file(s)
                        </span>
                      )}
                    </td>

                    <td className="px-6 py-4">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {(post.targetPlatforms as string[])?.map((p) => (
                          <span
                            key={p}
                            className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase ${
                              p === 'FACEBOOK'
                                ? 'bg-blue-50 text-blue-700 border border-blue-200'
                                : 'bg-red-50 text-red-700 border border-red-200'
                            }`}
                          >
                            {p}
                          </span>
                        ))}
                      </div>
                    </td>

                    <td className="px-6 py-4">
                      <span
                        className={`text-xs font-semibold px-2.5 py-1 rounded-full ${
                          post.status === 'PUBLISHED'
                            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                            : post.status === 'SCHEDULED'
                            ? 'bg-amber-50 text-amber-700 border border-amber-200'
                            : post.status === 'PARTIALLY_PUBLISHED'
                            ? 'bg-purple-50 text-purple-700 border border-purple-200'
                            : post.status === 'FAILED'
                            ? 'bg-rose-50 text-rose-700 border border-rose-200'
                            : 'bg-slate-100 text-slate-600'
                        }`}
                      >
                        {post.status}
                      </span>
                    </td>

                    <td className="px-6 py-4 text-xs text-slate-500">
                      {post.scheduledAt
                        ? `📅 ${new Date(post.scheduledAt).toLocaleString()}`
                        : new Date(post.createdAt).toLocaleDateString()}
                    </td>

                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {post.status !== 'PUBLISHED' && (
                          <button
                            onClick={() => publishMutation.mutate(post.id)}
                            disabled={publishMutation.isPending}
                            className="px-2.5 py-1 rounded bg-indigo-50 hover:bg-indigo-100 text-indigo-600 text-xs font-medium flex items-center gap-1 border border-indigo-200"
                            title="Publish Now Immediately"
                          >
                            <Send className="w-3 h-3" />
                            Publish
                          </button>
                        )}

                        <button
                          onClick={() => {
                            if (confirm('Delete this post?')) {
                              deleteMutation.mutate(post.id);
                            }
                          }}
                          className="p-1 rounded text-slate-400 hover:text-rose-600"
                          title="Delete Post"
                        >
                          <Trash2 className="w-4 h-4" />
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
    </div>
  );
};
