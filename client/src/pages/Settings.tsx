import React from 'react';
import { Shield, Key, Database, Server, ExternalLink, CheckCircle2 } from 'lucide-react';

export const Settings: React.FC = () => {
  return (
    <div className="space-y-8 max-w-5xl mx-auto">
      <div>
        <h2 className="text-2xl font-bold text-slate-900 tracking-tight">System Settings & Developer Guides</h2>
        <p className="text-sm text-slate-500 mt-1">
          OAuth credentials configuration, Meta App Review requirements, and Google Cloud settings.
        </p>
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
