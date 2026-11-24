import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  getSocialAccounts,
  createContent,
  uploadMediaFile,
} from '../api/client';
import {
  Send,
  Calendar,
  Save,
  UploadCloud,
  X,
  Share2,
  Check,
  Video,
  Image as ImageIcon,
  ThumbsUp,
  MessageCircle,
  Share,
  Play,
} from 'lucide-react';

export const CreatePost: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Form State
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['FACEBOOK', 'YOUTUBE']);
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  const [title, setTitle] = useState('');
  const [caption, setCaption] = useState('');
  const [description, setDescription] = useState('');
  const [hashtags, setHashtags] = useState('#video #trending #shorts');
  const [syncDescriptionWithCaption, setSyncDescriptionWithCaption] = useState(true);
  const [isScheduled, setIsScheduled] = useState(false);
  const [scheduledAt, setScheduledAt] = useState('');

  // Media State
  const [uploadedMedia, setUploadedMedia] = useState<any[]>([]);
  const [isUploading, setIsUploading] = useState(false);

  // Preview Tab
  const [previewTab, setPreviewTab] = useState<'FACEBOOK' | 'YOUTUBE'>('FACEBOOK');

  // Load connected accounts
  const { data: accounts } = useQuery({
    queryKey: ['social-accounts'],
    queryFn: getSocialAccounts,
  });

  const createMutation = useMutation({
    mutationFn: createContent,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contents'] });
      queryClient.invalidateQueries({ queryKey: ['dashboard-overview'] });
      navigate('/content');
    },
    onError: (err: any) => {
      alert(`Publishing error: ${err.response?.data?.message || err.message}`);
    },
  });

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    try {
      const media = await uploadMediaFile(file);
      setUploadedMedia((prev) => [...prev, media]);
    } catch (err: any) {
      alert(`Upload failed: ${err.response?.data?.message || err.message}`);
    } finally {
      setIsUploading(false);
    }
  };

  const togglePlatform = (p: string) => {
    setSelectedPlatforms((prev) =>
      prev.includes(p) ? prev.filter((item) => item !== p) : [...prev, p],
    );
  };

  const toggleAccount = (id: string) => {
    setSelectedAccountIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  const handleCaptionChange = (val: string) => {
    setCaption(val);
    if (syncDescriptionWithCaption) {
      setDescription(val);
    }
  };

  const handleSubmit = (action: 'publish' | 'schedule' | 'draft') => {
    if (selectedPlatforms.length === 0) {
      alert('Please select at least one target platform (Facebook or YouTube).');
      return;
    }

    if (action === 'schedule' && !scheduledAt) {
      alert('Please choose a future date and time for scheduled publishing.');
      return;
    }

    createMutation.mutate({
      title,
      caption,
      description,
      hashtags,
      targetPlatforms: selectedPlatforms,
      targetAccountIds: selectedAccountIds.length > 0 ? selectedAccountIds : undefined,
      mediaIds: uploadedMedia.map((m) => m.id),
      scheduledAt: action === 'schedule' ? scheduledAt : undefined,
      publishImmediately: action === 'publish',
    });
  };

  const activeFacebookAccount = accounts?.find(
    (a: any) => a.platform === 'FACEBOOK' && (selectedAccountIds.length === 0 || selectedAccountIds.includes(a.id)),
  );

  const activeYouTubeAccount = accounts?.find(
    (a: any) => a.platform === 'YOUTUBE' && (selectedAccountIds.length === 0 || selectedAccountIds.includes(a.id)),
  );

  const activeMedia = uploadedMedia[0];
  const isVideo = activeMedia?.mimeType?.startsWith('video/');

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Create & Schedule Post</h2>
        <p className="text-sm text-slate-500 mt-1">
          Compose multi-platform content with live, platform-accurate Facebook & YouTube previews.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        {/* ─── LEFT: FORM EDITOR (7 COLS) ─────────────────────────────────── */}
        <div className="lg:col-span-7 bg-white rounded-xl border border-slate-200 shadow-sm p-6 space-y-6">
          {/* Target Platforms */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-2.5">
              1. Target Platforms
            </label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => togglePlatform('FACEBOOK')}
                className={`flex items-center gap-3 p-3 rounded-lg border text-sm font-semibold transition-all ${
                  selectedPlatforms.includes('FACEBOOK')
                    ? 'border-[#1877F2] bg-blue-50/60 text-[#1877F2] ring-2 ring-[#1877F2]/20'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                }`}
              >
                <span className="w-6 h-6 rounded bg-[#1877F2] text-white flex items-center justify-center text-xs font-bold">
                  f
                </span>
                <span>Facebook Pages</span>
                {selectedPlatforms.includes('FACEBOOK') && <Check className="w-4 h-4 ml-auto" />}
              </button>

              <button
                type="button"
                onClick={() => togglePlatform('YOUTUBE')}
                className={`flex items-center gap-3 p-3 rounded-lg border text-sm font-semibold transition-all ${
                  selectedPlatforms.includes('YOUTUBE')
                    ? 'border-[#FF0000] bg-red-50/60 text-[#FF0000] ring-2 ring-[#FF0000]/20'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                }`}
              >
                <span className="w-6 h-6 rounded bg-[#FF0000] text-white flex items-center justify-center text-xs font-bold">
                  ▶
                </span>
                <span>YouTube Channels</span>
                {selectedPlatforms.includes('YOUTUBE') && <Check className="w-4 h-4 ml-auto" />}
              </button>
            </div>
          </div>

          {/* Account Selector */}
          {accounts && accounts.length > 0 && (
            <div>
              <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-2">
                Target Accounts ({accounts.filter((a: any) => selectedPlatforms.includes(a.platform)).length} available)
              </label>
              <div className="flex flex-wrap gap-2">
                {accounts
                  .filter((a: any) => selectedPlatforms.includes(a.platform))
                  .map((acc: any) => {
                    const isSelected = selectedAccountIds.includes(acc.id) || selectedAccountIds.length === 0;
                    return (
                      <button
                        key={acc.id}
                        type="button"
                        onClick={() => toggleAccount(acc.id)}
                        className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                          isSelected
                            ? 'bg-slate-900 text-white border-slate-900'
                            : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
                        }`}
                      >
                        <img src={acc.avatarUrl} alt="" className="w-4 h-4 rounded-full" />
                        <span>{acc.accountName}</span>
                      </button>
                    );
                  })}
              </div>
            </div>
          )}

          {/* Content Title */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">
              Post Title <span className="text-slate-400 font-normal">(Used for YouTube title & FB video header, max 100 chars)</span>
            </label>
            <input
              type="text"
              value={title}
              maxLength={100}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. 5 Tech Secrets You Must Know in 2026 #Shorts"
              className="w-full px-3.5 py-2.5 rounded-lg border border-slate-200 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-600"
            />
            <div className="text-right text-[11px] text-slate-400 mt-1">{title.length}/100</div>
          </div>

          {/* Caption / Primary Text */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">
              Caption / Primary Text <span className="text-slate-400 font-normal">(Main text for Facebook feed post)</span>
            </label>
            <textarea
              rows={4}
              value={caption}
              onChange={(e) => handleCaptionChange(e.target.value)}
              placeholder="Write your engaging post copy here..."
              className="w-full px-3.5 py-2.5 rounded-lg border border-slate-200 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-600"
            />
          </div>

          {/* YouTube Description */}
          {selectedPlatforms.includes('YOUTUBE') && (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
                  YouTube Description <span className="text-slate-400 font-normal">(Max 5,000 chars)</span>
                </label>
                <label className="text-xs text-indigo-600 flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={syncDescriptionWithCaption}
                    onChange={(e) => setSyncDescriptionWithCaption(e.target.checked)}
                    className="rounded text-indigo-600 focus:ring-indigo-500"
                  />
                  Sync with caption
                </label>
              </div>
              <textarea
                rows={3}
                value={description}
                disabled={syncDescriptionWithCaption}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Detailed YouTube description box text..."
                className={`w-full px-3.5 py-2.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-600 ${
                  syncDescriptionWithCaption ? 'bg-slate-50 text-slate-500' : 'text-slate-900'
                }`}
              />
            </div>
          )}

          {/* Hashtags */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">
              Hashtags / Video Tags
            </label>
            <input
              type="text"
              value={hashtags}
              onChange={(e) => setHashtags(e.target.value)}
              placeholder="#viral #technology #reels #shorts"
              className="w-full px-3.5 py-2 rounded-lg border border-slate-200 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-600"
            />
          </div>

          {/* Media Attachments */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1.5">
              Media Attachment <span className="text-slate-400 font-normal">(Images or Video - stored in S3/MinIO)</span>
            </label>

            {uploadedMedia.length === 0 ? (
              <label className="border-2 border-dashed border-slate-200 rounded-xl p-8 flex flex-col items-center justify-center text-center cursor-pointer hover:border-indigo-400 hover:bg-indigo-50/20 transition-all">
                <UploadCloud className="w-8 h-8 text-slate-400 mb-2" />
                <span className="text-xs font-semibold text-slate-700">
                  {isUploading ? 'Uploading to S3/MinIO...' : 'Click or drop video / image here'}
                </span>
                <span className="text-[11px] text-slate-400 mt-1">
                  Supports MP4, MOV, WebM (up to 1GB) and JPEG, PNG (up to 50MB)
                </span>
                <input
                  type="file"
                  accept="image/*,video/*"
                  onChange={handleFileUpload}
                  disabled={isUploading}
                  className="hidden"
                />
              </label>
            ) : (
              <div className="flex items-center gap-3 p-3 rounded-lg border border-slate-200 bg-slate-50">
                {isVideo ? (
                  <Video className="w-8 h-8 text-indigo-600 shrink-0" />
                ) : (
                  <ImageIcon className="w-8 h-8 text-indigo-600 shrink-0" />
                )}
                <div className="truncate flex-1">
                  <p className="text-xs font-semibold text-slate-800 truncate">{activeMedia.originalName}</p>
                  <p className="text-[11px] text-slate-400">
                    {(activeMedia.fileSize / 1024 / 1024).toFixed(2)} MB • {activeMedia.mimeType}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setUploadedMedia([])}
                  className="p-1 rounded text-slate-400 hover:text-rose-600"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

          {/* Scheduling Toggle */}
          <div className="pt-2 border-t border-slate-100">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-xs font-semibold text-slate-800">Schedule for Future Publishing</span>
                <p className="text-[11px] text-slate-400">Posts will automatically publish via Redis BullMQ</p>
              </div>
              <input
                type="checkbox"
                checked={isScheduled}
                onChange={(e) => setIsScheduled(e.target.checked)}
                className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500"
              />
            </div>

            {isScheduled && (
              <div className="mt-3">
                <input
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(e) => setScheduledAt(e.target.value)}
                  className="px-3.5 py-2 rounded-lg border border-slate-200 text-xs text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                />
              </div>
            )}
          </div>

          {/* Bottom Actions */}
          <div className="flex items-center justify-between pt-4 border-t border-slate-100 gap-3">
            <button
              type="button"
              onClick={() => handleSubmit('draft')}
              disabled={createMutation.isPending}
              className="px-4 py-2 rounded-lg border border-slate-200 text-slate-700 text-xs font-semibold hover:bg-slate-50 transition-all flex items-center gap-1.5"
            >
              <Save className="w-3.5 h-3.5" />
              Save Draft
            </button>

            <div className="flex items-center gap-2">
              {isScheduled ? (
                <button
                  type="button"
                  onClick={() => handleSubmit('schedule')}
                  disabled={createMutation.isPending}
                  className="px-5 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold shadow-sm transition-all flex items-center gap-1.5 shadow-amber-500/20"
                >
                  <Calendar className="w-3.5 h-3.5" />
                  Schedule Post
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleSubmit('publish')}
                  disabled={createMutation.isPending}
                  className="px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition-all flex items-center gap-1.5 shadow-indigo-500/20"
                >
                  <Send className="w-3.5 h-3.5" />
                  Publish Now
                </button>
              )}
            </div>
          </div>
        </div>

        {/* ─── RIGHT: LIVE PLATFORM PREVIEWS (5 COLS) ──────────────────────── */}
        <div className="lg:col-span-5 space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Live Platform Preview</span>
            <div className="flex rounded-lg border border-slate-200 bg-white p-0.5">
              <button
                type="button"
                onClick={() => setPreviewTab('FACEBOOK')}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition-all ${
                  previewTab === 'FACEBOOK' ? 'bg-[#1877F2] text-white shadow-sm' : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                Facebook
              </button>
              <button
                type="button"
                onClick={() => setPreviewTab('YOUTUBE')}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition-all ${
                  previewTab === 'YOUTUBE' ? 'bg-[#FF0000] text-white shadow-sm' : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                YouTube
              </button>
            </div>
          </div>

          {/* ─── FACEBOOK PREVIEW CARD ─────────────────────────────────────── */}
          {previewTab === 'FACEBOOK' && (
            <div className="bg-white rounded-xl border border-slate-200/90 shadow-sm overflow-hidden text-slate-900">
              {/* FB Card Header */}
              <div className="p-4 flex items-center gap-3">
                <img
                  src={activeFacebookAccount?.avatarUrl || 'https://via.placeholder.com/40'}
                  alt=""
                  className="w-10 h-10 rounded-full object-cover border border-slate-200"
                />
                <div>
                  <h4 className="text-xs font-bold leading-tight text-slate-900">
                    {activeFacebookAccount?.accountName || 'Your Facebook Page'}
                  </h4>
                  <p className="text-[11px] text-slate-500 flex items-center gap-1 mt-0.5">
                    Just now • <span>🌐 Public</span>
                  </p>
                </div>
              </div>

              {/* FB Post Text */}
              <div className="px-4 pb-3 text-xs leading-relaxed text-slate-800 whitespace-pre-wrap">
                {caption || 'What’s on your mind?'}
                {hashtags && <span className="text-blue-600 font-medium block mt-1">{hashtags}</span>}
              </div>

              {/* FB Media Preview */}
              {activeMedia ? (
                <div className="bg-slate-900 aspect-video flex items-center justify-center relative overflow-hidden">
                  {isVideo ? (
                    <div className="relative w-full h-full flex items-center justify-center bg-slate-950">
                      <video src={activeMedia.url} controls className="max-h-full max-w-full" />
                    </div>
                  ) : (
                    <img src={activeMedia.url} alt="" className="w-full h-full object-cover" />
                  )}
                </div>
              ) : (
                <div className="bg-slate-100 aspect-video flex flex-col items-center justify-center text-slate-400 text-xs border-y border-slate-100">
                  <ImageIcon className="w-8 h-8 mb-1 opacity-50" />
                  <span>Media attachment preview</span>
                </div>
              )}

              {/* FB Like/Comment/Share bar */}
              <div className="px-4 py-2 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500 font-medium">
                <button className="flex-1 py-1.5 flex items-center justify-center gap-1.5 hover:bg-slate-50 rounded">
                  <ThumbsUp className="w-4 h-4" /> Like
                </button>
                <button className="flex-1 py-1.5 flex items-center justify-center gap-1.5 hover:bg-slate-50 rounded">
                  <MessageCircle className="w-4 h-4" /> Comment
                </button>
                <button className="flex-1 py-1.5 flex items-center justify-center gap-1.5 hover:bg-slate-50 rounded">
                  <Share className="w-4 h-4" /> Share
                </button>
              </div>
            </div>
          )}

          {/* ─── YOUTUBE PREVIEW CARD ──────────────────────────────────────── */}
          {previewTab === 'YOUTUBE' && (
            <div className="bg-white rounded-xl border border-slate-200/90 shadow-sm overflow-hidden text-slate-900">
              {/* YT Video Display */}
              <div className="bg-slate-950 aspect-video flex items-center justify-center relative overflow-hidden text-white">
                {activeMedia && isVideo ? (
                  <video src={activeMedia.url} controls className="max-h-full max-w-full" />
                ) : (
                  <div className="flex flex-col items-center justify-center text-slate-500 text-xs">
                    <Play className="w-12 h-12 mb-2 opacity-50" />
                    <span>YouTube Video Player</span>
                    <span className="text-[10px] text-slate-600 mt-1">Requires video attachment</span>
                  </div>
                )}
                {title.toLowerCase().includes('#shorts') && (
                  <span className="absolute top-2 left-2 bg-[#FF0000] text-white text-[10px] font-bold px-2 py-0.5 rounded shadow">
                    SHORTS
                  </span>
                )}
              </div>

              {/* YT Meta Info */}
              <div className="p-4 space-y-3">
                <h4 className="font-bold text-sm text-slate-900 leading-snug">
                  {title || 'Untitled YouTube Video'}
                </h4>

                <div className="flex items-center justify-between gap-3 pt-2 border-t border-slate-100">
                  <div className="flex items-center gap-2.5">
                    <img
                      src={activeYouTubeAccount?.avatarUrl || 'https://via.placeholder.com/36'}
                      alt=""
                      className="w-8 h-8 rounded-full border border-slate-200 object-cover"
                    />
                    <div>
                      <p className="text-xs font-bold text-slate-900 leading-tight">
                        {activeYouTubeAccount?.accountName || 'Your Channel'}
                      </p>
                      <p className="text-[10px] text-slate-400">10.5K subscribers</p>
                    </div>
                  </div>

                  <button className="px-3 py-1 rounded-full bg-slate-900 text-white text-xs font-semibold">
                    Subscribe
                  </button>
                </div>

                {/* YT Description Box Preview */}
                <div className="bg-slate-50 rounded-lg p-2.5 text-[11px] text-slate-600 space-y-1 border border-slate-100">
                  <p className="font-semibold text-slate-700">Description Box Preview</p>
                  <p className="line-clamp-2">{description || 'No description provided'}</p>
                  {hashtags && <p className="text-blue-600 font-medium">{hashtags}</p>}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
