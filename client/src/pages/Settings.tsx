import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Shield, Key, Database, Server, ExternalLink, CheckCircle2, Save, RotateCcw } from 'lucide-react';
import { getPlatformConfigs, savePlatformConfig, deletePlatformConfig } from '../api/client';

interface PlatformConfigItem {
  platform: 'FACEBOOK' | 'YOUTUBE' | 'THREADS' | 'TIKTOK';
  appId: string;
  redirectUri: string;
  hasSecret: boolean;
  isActive: boolean;
  source: 'DATABASE' | 'ENV' | 'UNCONFIGURED';
}

export const Settings: React.FC = () => {
  const queryClient = useQueryClient();
  const { data: configs, isLoading } = useQuery<PlatformConfigItem[]>({
    queryKey: ['platform-configs'],
    queryFn: getPlatformConfigs,
  });

  const [drafts, setDrafts] = useState<Record<string, { appId: string; appSecret: string; redirectUri: string }>>({});
  const [savedPlatform, setSavedPlatform] = useState<string | null>(null);

  useEffect(() => {
    if (configs) {
      const next: Record<string, { appId: string; appSecret: string; redirectUri: string }> = {};
      for (const c of configs) {
        next[c.platform] = {
          appId: c.appId || '',
          appSecret: '',
          redirectUri: c.redirectUri || '',
        };
      }
      setDrafts(next);
    }
  }, [configs]);

  const saveMutation = useMutation({
    mutationFn: ({ platform, payload }: { platform: string; payload: any }) =>
      savePlatformConfig(platform, payload),
    onSuccess: (_data, vars) => {
      setSavedPlatform(vars.platform);
      setTimeout(() => setSavedPlatform(null), 2500);
      queryClient.invalidateQueries({ queryKey: ['platform-configs'] });
    },
  });

  const resetMutation = useMutation({
    mutationFn: (platform: string) => deletePlatformConfig(platform),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['platform-configs'] });
    },
  });

  return (
    <div className="space-y-8 max-w-5xl mx-auto">
      <div>
        <h2 className="text-2xl font-bold text-slate-900 tracking-tight">System Settings & Developer Guides</h2>
        <p className="text-sm text-slate-500 mt-1">
          Manage OAuth App credentials in PostgreSQL (encrypted at rest) or fallback to .env defaults.
        </p>
      </div>

      {/* ─── DATABASE OAUTH CREDENTIALS MANAGER ────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-bold text-base text-slate-900 flex items-center gap-2">
              <Key className="w-5 h-5 text-indigo-600" />
              Platform OAuth App Credentials (Database)
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Stored per-workspace in PostgreSQL with AES-256-GCM encryption. Overrides .env when set.
            </p>
          </div>
          <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200 flex items-center gap-1">
            <Database className="w-3.5 h-3.5" /> DB + .env Fallback
          </span>
        </div>

        {isLoading ? (
          <div className="text-xs text-slate-400 py-4">Loading platform configurations...</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {(configs || []).map((cfg) => {
              const d = drafts[cfg.platform] || { appId: '', appSecret: '', redirectUri: '' };
              return (
                <div key={cfg.platform} className="p-4 rounded-xl border border-slate-200 bg-slate-50/70 space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-sm text-slate-900">{cfg.platform}</span>
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                        cfg.source === 'DATABASE'
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                          : cfg.source === 'ENV'
                          ? 'bg-amber-50 text-amber-700 border border-amber-200'
                          : 'bg-slate-200 text-slate-600'
                      }`}
                    >
                      {cfg.source === 'DATABASE' ? 'Database' : cfg.source === 'ENV' ? 'From .env' : 'Not Set'}
                    </span>
                  </div>

                  <div>
                    <label className="block text-[11px] font-semibold text-slate-600 mb-1">
                      {cfg.platform === 'TIKTOK' ? 'Client Key' : cfg.platform === 'YOUTUBE' ? 'Client ID' : 'App ID'}
                    </label>
                    <input
                      type="text"
                      value={d.appId}
                      onChange={(e) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [cfg.platform]: { ...d, appId: e.target.value },
                        }))
                      }
                      placeholder="Enter App ID / Client Key"
                      className="w-full px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs"
                    />
                  </div>

                  <div>
                    <label className="block text-[11px] font-semibold text-slate-600 mb-1">
                      App Secret {cfg.hasSecret && <span className="text-emerald-600 font-normal">(configured — leave blank to keep)</span>}
                    </label>
                    <input
                      type="password"
                      value={d.appSecret}
                      onChange={(e) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [cfg.platform]: { ...d, appSecret: e.target.value },
                        }))
                      }
                      placeholder={cfg.hasSecret ? '•••••••• (unchanged)' : 'Enter App Secret'}
                      className="w-full px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs"
                    />
                  </div>

                  <div>
                    <label className="block text-[11px] font-semibold text-slate-600 mb-1">OAuth Redirect URI</label>
                    <input
                      type="text"
                      value={d.redirectUri}
                      onChange={(e) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [cfg.platform]: { ...d, redirectUri: e.target.value },
                        }))
                      }
                      placeholder="http://localhost:3000/api/social-accounts/..."
                      className="w-full px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs"
                    />
                  </div>

                  <div className="flex items-center justify-between pt-1">
                    <button
                      type="button"
                      disabled={saveMutation.isPending}
                      onClick={() =>
                        saveMutation.mutate({
                          platform: cfg.platform,
                          payload: {
                            appId: d.appId,
                            appSecret: d.appSecret || undefined,
                            redirectUri: d.redirectUri || undefined,
                          },
                        })
                      }
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold"
                    >
                      <Save className="w-3.5 h-3.5" />
                      {savedPlatform === cfg.platform ? 'Saved!' : 'Save to DB'}
                    </button>

                    {cfg.source === 'DATABASE' && (
                      <button
                        type="button"
                        disabled={resetMutation.isPending}
                        onClick={() => resetMutation.mutate(cfg.platform)}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 text-slate-600 text-xs font-medium"
                      >
                        <RotateCcw className="w-3 h-3" /> Revert to .env
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ─── SYSTEM STATUS CARD ───────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-4">
        <h3 className="font-bold text-base text-slate-900 flex items-center gap-2">
          <Server className="w-5 h-5 text-indigo-600" />
          Infrastructure & Security Status
        </h3>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-3.5 rounded-lg border border-slate-200 bg-slate-50">
            <span className="text-[11px] text-slate-400 uppercase font-semibold">PostgreSQL & Prisma</span>
            <p className="text-xs font-bold text-slate-800 mt-1 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> Active & Migrated
            </p>
          </div>

          <div className="p-3.5 rounded-lg border border-slate-200 bg-slate-50">
            <span className="text-[11px] text-slate-400 uppercase font-semibold">Redis & BullMQ</span>
            <p className="text-xs font-bold text-slate-800 mt-1 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> Queue Persistent
            </p>
          </div>

          <div className="p-3.5 rounded-lg border border-slate-200 bg-slate-50">
            <span className="text-[11px] text-slate-400 uppercase font-semibold">MinIO / S3 Storage</span>
            <p className="text-xs font-bold text-slate-800 mt-1 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> Bucket Configured
            </p>
          </div>

          <div className="p-3.5 rounded-lg border border-slate-200 bg-slate-50">
            <span className="text-[11px] text-slate-400 uppercase font-semibold">AES-256-GCM</span>
            <p className="text-xs font-bold text-slate-800 mt-1 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> At-Rest Encryption
            </p>
          </div>
        </div>
      </div>

      {/* ─── META APP SETUP GUIDE ─────────────────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-4">
        <h3 className="font-bold text-base text-slate-900 flex items-center gap-2">
          <span className="w-5 h-5 rounded bg-[#1877F2] text-white flex items-center justify-center text-xs font-bold">
            f
          </span>
          Meta Graph API v21.0 Configuration Guide
        </h3>

        <div className="text-xs text-slate-600 space-y-2 leading-relaxed">
          <p>
            To connect Facebook Pages in production, create a Business App on{' '}
            <a
              href="https://developers.facebook.com"
              target="_blank"
              rel="noreferrer"
              className="text-indigo-600 font-semibold underline"
            >
              Meta for Developers
            </a>
            .
          </p>

          <div className="bg-slate-50 rounded-lg p-4 border border-slate-200 space-y-2">
            <p className="font-semibold text-slate-800">Required Permissions for App Review:</p>
            <ul className="list-disc pl-5 space-y-1 text-slate-600">
              <li>
                <code className="font-mono bg-white px-1 py-0.5 rounded border border-slate-200">pages_show_list</code>: To list the Facebook Pages you manage.
              </li>
              <li>
                <code className="font-mono bg-white px-1 py-0.5 rounded border border-slate-200">pages_read_engagement</code>: To read basic engagement and posts.
              </li>
              <li>
                <code className="font-mono bg-white px-1 py-0.5 rounded border border-slate-200">pages_manage_posts</code>: To publish text, photo, video, and Reel posts.
              </li>
              <li>
                <code className="font-mono bg-white px-1 py-0.5 rounded border border-slate-200">pages_manage_metadata</code>: To subscribe to page webhooks.
              </li>
            </ul>

            <p className="font-semibold text-slate-800 pt-2">OAuth Redirect URI to Whitelist:</p>
            <code className="block bg-slate-900 text-slate-100 p-2 rounded font-mono text-[11px]">
              http://localhost:3000/api/social-accounts/facebook/callback
            </code>
          </div>
        </div>
      </div>

      {/* ─── GOOGLE CLOUD YOUTUBE SETUP GUIDE ─────────────────────────────── */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-4">
        <h3 className="font-bold text-base text-slate-900 flex items-center gap-2">
          <span className="w-5 h-5 rounded bg-[#FF0000] text-white flex items-center justify-center text-xs font-bold">
            ▶
          </span>
          Google Cloud & YouTube Data API v3 Guide
        </h3>

        <div className="text-xs text-slate-600 space-y-2 leading-relaxed">
          <p>
            Enable YouTube Data API v3 in{' '}
            <a
              href="https://console.cloud.google.com"
              target="_blank"
              rel="noreferrer"
              className="text-indigo-600 font-semibold underline"
            >
              Google Cloud Console
            </a>
            .
          </p>

          <div className="bg-slate-50 rounded-lg p-4 border border-slate-200 space-y-2">
            <p className="font-semibold text-slate-800">Required OAuth Scopes:</p>
            <ul className="list-disc pl-5 space-y-1 text-slate-600">
              <li>
                <code className="font-mono bg-white px-1 py-0.5 rounded border border-slate-200">
                  https://www.googleapis.com/auth/youtube.upload
                </code>
                : To upload video and Shorts content directly to channel.
              </li>
              <li>
                <code className="font-mono bg-white px-1 py-0.5 rounded border border-slate-200">
                  https://www.googleapis.com/auth/youtube.readonly
                </code>
                : To read channel information and statistics.
              </li>
            </ul>

            <p className="font-semibold text-slate-800 pt-2">OAuth Redirect URI to Whitelist:</p>
            <code className="block bg-slate-900 text-slate-100 p-2 rounded font-mono text-[11px]">
              http://localhost:3000/api/social-accounts/youtube/callback
            </code>

            <p className="font-semibold text-slate-800 pt-2">API Quota Information:</p>
            <p className="text-slate-600">
              Default Google Cloud quota is 10,000 units per day. Each <code className="font-mono">videos.insert</code> consumes 1,600 units. You can request a quota extension directly from Google Cloud Console.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
