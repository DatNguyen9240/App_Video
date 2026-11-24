import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getMediaList, uploadMediaFile, deleteMediaItem } from '../api/client';
import { UploadCloud, Trash2, Video, Image as ImageIcon, ExternalLink, HardDrive } from 'lucide-react';

export const MediaLibrary: React.FC = () => {
  const queryClient = useQueryClient();
  const [isUploading, setIsUploading] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['media-library'],
    queryFn: () => getMediaList(),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteMediaItem,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['media-library'] }),
  });

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    try {
      await uploadMediaFile(file);
      queryClient.invalidateQueries({ queryKey: ['media-library'] });
    } catch (err: any) {
      alert(`Upload failed: ${err.response?.data?.message || err.message}`);
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* ─── HEADER ───────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 tracking-tight">Media Asset Library</h2>
          <p className="text-sm text-slate-500 mt-1">
            Store, preview, and reuse videos and images in MinIO / S3 storage.
          </p>
        </div>

        <label className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition-all cursor-pointer">
          <UploadCloud className="w-4 h-4" />
          <span>{isUploading ? 'Uploading to S3...' : 'Upload Media File'}</span>
          <input
            type="file"
            accept="image/*,video/*"
            onChange={handleUpload}
            disabled={isUploading}
            className="hidden"
          />
        </label>
      </div>

      {/* ─── MEDIA GRID ───────────────────────────────────────────────────── */}
      {isLoading ? (
        <div className="p-16 text-center text-slate-400 text-sm">Loading media library...</div>
      ) : !data?.data || data.data.length === 0 ? (
        <div className="bg-white rounded-xl border border-dashed border-slate-200 p-16 text-center">
          <HardDrive className="w-12 h-12 text-slate-300 mx-auto mb-3" />
          <h4 className="font-semibold text-slate-700 text-sm">No media files uploaded</h4>
          <p className="text-xs text-slate-400 max-w-sm mx-auto mt-1 mb-4">
            Upload videos or images to MinIO/S3 storage to attach them to Facebook and YouTube campaigns.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
          {data.data.map((item: any) => {
            const isVideo = item.mimeType?.startsWith('video/');
            return (
              <div
                key={item.id}
                className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden group hover:shadow-md transition-all flex flex-col"
              >
                <div className="aspect-video bg-slate-900 relative overflow-hidden flex items-center justify-center">
                  {isVideo ? (
                    <video src={item.url} className="w-full h-full object-cover" />
                  ) : (
                    <img src={item.url} alt="" className="w-full h-full object-cover" />
                  )}
                  <span className="absolute top-2 left-2 px-2 py-0.5 rounded bg-black/60 text-white text-[10px] font-semibold uppercase backdrop-blur-sm">
                    {isVideo ? 'Video' : 'Image'}
                  </span>
                </div>

                <div className="p-4 flex-1 flex flex-col justify-between">
                  <div>
                    <h4 className="font-bold text-xs text-slate-900 truncate" title={item.originalName}>
                      {item.originalName}
                    </h4>
                    <p className="text-[11px] text-slate-400 mt-0.5">
                      {(item.fileSize / 1024 / 1024).toFixed(2)} MB • {item.mimeType}
                    </p>
                  </div>

                  <div className="flex items-center justify-between pt-3 mt-3 border-t border-slate-100">
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-indigo-600 hover:text-indigo-700 font-medium flex items-center gap-1"
                    >
                      View <ExternalLink className="w-3 h-3" />
                    </a>
                    <button
                      onClick={() => {
                        if (confirm(`Delete ${item.originalName}?`)) {
                          deleteMutation.mutate(item.id);
                        }
                      }}
                      className="p-1 rounded text-slate-400 hover:text-rose-600 transition-colors"
                      title="Delete File"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
