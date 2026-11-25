import React from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getSocialAccounts,
  getFacebookAuthUrl,
  getYouTubeAuthUrl,
  getThreadsAuthUrl,
  connectThreadsManualToken,
  getTikTokAuthUrl,
  connectTikTokManualToken,
  disconnectSocialAccount,
  refreshSocialAccountToken,
} from '../api/client';
import {
  Share2,
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  ShieldCheck,
} from 'lucide-react';

export const ConnectedAccounts: React.FC = () => {
  const queryClient = useQueryClient();

  const { data: accounts, isLoading } = useQuery({
    queryKey: ['social-accounts'],
    queryFn: getSocialAccounts,
  });

  const disconnectMutation = useMutation({
    mutationFn: disconnectSocialAccount,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['social-accounts'] }),
  });

  const refreshMutation = useMutation({
    mutationFn: refreshSocialAccountToken,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['social-accounts'] }),
  });

  const handleConnectFacebook = async () => {
    try {
      const res = await getFacebookAuthUrl();
      if (res.url) window.location.href = res.url;
    } catch (err: any) {
      alert(`Failed to start Facebook OAuth: ${err.response?.data?.message || err.message}`);
    }
  };

  const handleConnectYouTube = async () => {
    try {
      const res = await getYouTubeAuthUrl();
      if (res.url) window.location.href = res.url;
    } catch (err: any) {
      alert(`Failed to start YouTube OAuth: ${err.response?.data?.message || err.message}`);
    }
  };

  const handleConnectThreads = async () => {
    const manualToken = prompt('Dán Threads Access Token (từ mục "Công cụ tạo mã người dùng" trên Meta Dashboard) hoặc bấm Cancel để mở OAuth popup:');
    if (manualToken && manualToken.trim()) {
      try {
        await connectThreadsManualToken(manualToken.trim());
        queryClient.invalidateQueries({ queryKey: ['social-accounts'] });
        alert('🎉 Kết nối tài khoản Threads thành công!');
        return;
      } catch (err: any) {
        alert(`Lỗi kết nối Threads: ${err.response?.data?.message || err.message}`);
        return;
      }
    }
    try {
      const res = await getThreadsAuthUrl();
      if (res.url) window.location.href = res.url;
    } catch (err: any) {
      alert(`Failed to start Threads OAuth: ${err.response?.data?.message || err.message}`);
    }
  };

  const handleConnectTikTok = async () => {
    const manualToken = prompt('Dán TikTok Access Token (từ TikTok Developer Portal) hoặc bấm Cancel để mở OAuth popup:');
    if (manualToken && manualToken.trim()) {
      try {
        await connectTikTokManualToken(manualToken.trim());
        queryClient.invalidateQueries({ queryKey: ['social-accounts'] });
        alert('🎉 Kết nối tài khoản TikTok thành công!');
        return;
      } catch (err: any) {
        alert(`Lỗi kết nối TikTok: ${err.response?.data?.message || err.message}`);
        return;
      }
    }
    try {
      const res = await getTikTokAuthUrl();
      if (res.url) window.location.href = res.url;
    } catch (err: any) {
      alert(`Failed to start TikTok OAuth: ${err.response?.data?.message || err.message}`);
    }
  };

  return (
    <div className="space-y-8 max-w-6xl mx-auto">
      {/* ─── HEADER ───────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Connected Social Accounts</h2>
          <p className="text-sm text-slate-500 mt-1">
            Authorize Meta Facebook Pages, Google YouTube Channels, Meta Threads, and TikTok via official APIs.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleConnectFacebook}
            className="inline-flex items-center gap-2 px-3.5 py-2.5 rounded-lg bg-[#1877F2] hover:bg-[#166fe5] text-white text-xs font-semibold shadow-sm transition-all"
          >
            <Share2 className="w-3.5 h-3.5" />
            Connect Facebook
          </button>
          <button
            onClick={handleConnectThreads}
            className="inline-flex items-center gap-2 px-3.5 py-2.5 rounded-lg bg-black hover:bg-neutral-800 text-white text-xs font-semibold shadow-sm transition-all"
          >
            <span className="font-bold">@</span>
            Connect Threads
          </button>
          <button
            onClick={handleConnectTikTok}
            className="inline-flex items-center gap-2 px-3.5 py-2.5 rounded-lg bg-[#010101] hover:bg-[#1a1a1a] text-[#00f2fe] border border-neutral-700 text-xs font-semibold shadow-sm transition-all"
          >
            <span className="text-[#fe2c55] font-extrabold text-sm">♪</span>
            <span className="text-white">Connect TikTok</span>
          </button>
          <button
            onClick={handleConnectYouTube}
            className="inline-flex items-center gap-2 px-3.5 py-2.5 rounded-lg bg-[#FF0000] hover:bg-[#e60000] text-white text-xs font-semibold shadow-sm transition-all"
          >
            <Share2 className="w-3.5 h-3.5" />
            Connect YouTube
          </button>
        </div>
      </div>

      {/* ─── ACCOUNTS LIST ────────────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200/80 shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
          <span className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
            Active Accounts ({accounts?.length ?? 0})
          </span>
          <span className="text-xs text-slate-400 flex items-center gap-1">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
            Encrypted with AES-256-GCM at Rest
          </span>
        </div>

        <div className="divide-y divide-slate-100">
          {isLoading ? (
            <div className="p-12 text-center text-slate-400 text-sm">Loading connected accounts...</div>
          ) : !accounts || accounts.length === 0 ? (
            <div className="p-16 text-center">
              <div className="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center text-slate-400 mx-auto mb-3">
                <Share2 className="w-6 h-6" />
              </div>
              <h4 className="font-semibold text-slate-800 text-sm">No social accounts connected</h4>
              <p className="text-xs text-slate-400 max-w-sm mx-auto mt-1 mb-6">
                Connect your Facebook Pages or YouTube Channels above to start automated, scheduled publishing.
              </p>
              <div className="flex items-center justify-center gap-3">
                <button
                  onClick={handleConnectFacebook}
                  className="px-4 py-2 rounded-lg bg-[#1877F2] text-white text-xs font-semibold hover:bg-[#166fe5]"
                >
                  Connect Facebook
                </button>
                <button
                  onClick={handleConnectYouTube}
                  className="px-4 py-2 rounded-lg bg-[#FF0000] text-white text-xs font-semibold hover:bg-[#e60000]"
                >
                  Connect YouTube
                </button>
              </div>
            </div>
          ) : (
            accounts.map((acc: any) => (
              <div key={acc.id} className="p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4 hover:bg-slate-50/50 transition-colors">
                <div className="flex items-center gap-4">
                  <div className="relative">
                    <img
                      src={acc.avatarUrl || 'https://via.placeholder.com/80'}
                      alt={acc.accountName}
                      className="w-14 h-14 rounded-full object-cover border border-slate-200 shadow-sm"
                    />
                    <span
                      className={`absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center text-[10px] text-white font-bold shadow-sm ${
                        acc.platform === 'FACEBOOK'
                          ? 'bg-[#1877F2]'
                          : acc.platform === 'THREADS'
                          ? 'bg-black'
                          : acc.platform === 'TIKTOK'
                          ? 'bg-black text-[#00f2fe]'
                          : 'bg-[#FF0000]'
                      }`}
                    >
                      {acc.platform === 'FACEBOOK' ? 'f' : acc.platform === 'THREADS' ? '@' : acc.platform === 'TIKTOK' ? '♪' : '▶'}
                    </span>
                  </div>

                  <div>
                    <div className="flex items-center gap-2">
                      <h4 className="font-bold text-slate-900 text-base">{acc.accountName}</h4>
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider ${
                          acc.status === 'CONNECTED'
                            ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                            : 'bg-rose-50 text-rose-700 border border-rose-200'
                        }`}
                      >
                        {acc.status}
                      </span>
                    </div>
                    <p className="text-xs text-slate-500 mt-0.5">
                      Platform ID: <code className="bg-slate-100 px-1 py-0.5 rounded text-slate-700">{acc.platformAccountId}</code>
                    </p>
                    {acc.metadata?.subscriberCount && (
                      <p className="text-[11px] text-slate-400 mt-1">
                        Subscribers: {Number(acc.metadata.subscriberCount).toLocaleString()} • Videos: {acc.metadata.videoCount}
                      </p>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2.5">
                  <button
                    onClick={() => refreshMutation.mutate(acc.id)}
                    disabled={refreshMutation.isPending}
                    className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 text-slate-600 text-xs font-medium flex items-center gap-1.5 shadow-sm transition-all"
                    title="Refresh OAuth Token"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${refreshMutation.isPending ? 'animate-spin' : ''}`} />
                    Refresh Token
                  </button>

                  <button
                    onClick={() => {
                      if (confirm(`Are you sure you want to disconnect ${acc.accountName}?`)) {
                        disconnectMutation.mutate(acc.id);
                      }
                    }}
                    disabled={disconnectMutation.isPending}
                    className="px-3 py-1.5 rounded-lg border border-rose-200 bg-white hover:bg-rose-50 text-rose-600 text-xs font-medium flex items-center gap-1.5 shadow-sm transition-all"
                    title="Disconnect Account"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    Disconnect
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
};
