import axios from 'axios';

export const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
    'x-user-id': 'demo-user-id-00000000-0000-0000',
  },
});

if (api.interceptors?.request?.use) {
  api.interceptors.request.use((config) => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('autopub_token') : null;
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });
}

export const loginUser = (data: { email: string; password: string }) =>
  api.post('/auth/login', data).then((r) => r.data);
export const registerUser = (data: { email: string; password: string; name?: string }) =>
  api.post('/auth/register', data).then((r) => r.data);
export const getCurrentUser = () => api.get('/auth/me').then((r) => r.data);

export const getPlatformConfigs = () => api.get('/settings/platform-configs').then((r) => r.data);
export const savePlatformConfig = (
  platform: string,
  data: { appId: string; appSecret?: string; redirectUri?: string; isActive?: boolean },
) => api.put(`/settings/platform-configs/${platform}`, data).then((r) => r.data);
export const deletePlatformConfig = (platform: string) =>
  api.delete(`/settings/platform-configs/${platform}`).then((r) => r.data);

export const getOverview = () => api.get('/dashboard/overview').then((r) => r.data);
export const getRecentActivity = (limit = 10) =>
  api.get(`/dashboard/recent-activity?limit=${limit}`).then((r) => r.data);
export const getUpcomingQueue = (limit = 10) =>
  api.get(`/dashboard/upcoming-queue?limit=${limit}`).then((r) => r.data);

export const getSocialAccounts = () => api.get('/social-accounts').then((r) => r.data);
export const getFacebookAuthUrl = () => api.get('/social-accounts/facebook/connect').then((r) => r.data);
export const getYouTubeAuthUrl = () => api.get('/social-accounts/youtube/connect').then((r) => r.data);
export const getThreadsAuthUrl = () => api.get('/social-accounts/threads/connect').then((r) => r.data);
export const connectThreadsManualToken = (token: string) => api.post('/social-accounts/threads/manual-token', { token }).then((r) => r.data);
export const getTikTokAuthUrl = () => api.get('/social-accounts/tiktok/connect').then((r) => r.data);
export const connectTikTokManualToken = (token: string) => api.post('/social-accounts/tiktok/manual-token', { token }).then((r) => r.data);
export const disconnectSocialAccount = (id: string) => api.delete(`/social-accounts/${id}`).then((r) => r.data);
export const refreshSocialAccountToken = (id: string) => api.post(`/social-accounts/${id}/refresh`).then((r) => r.data);

export const getContents = (params?: { status?: string; page?: number; limit?: number }) =>
  api.get('/contents', { params }).then((r) => r.data);
export const getContentById = (id: string) => api.get(`/contents/${id}`).then((r) => r.data);
export const createContent = (data: any) => api.post('/contents', data).then((r) => r.data);
export const updateContent = (id: string, data: any) => api.patch(`/contents/${id}`, data).then((r) => r.data);
export const deleteContent = (id: string) => api.delete(`/contents/${id}`).then((r) => r.data);

export const publishNow = (contentId: string) => api.post('/publishing/publish-now', { contentId }).then((r) => r.data);
export const schedulePublish = (contentId: string, scheduledAt: string) =>
  api.post('/publishing/schedule', { contentId, scheduledAt }).then((r) => r.data);
export const getPublishingTasks = (params?: { platform?: string; status?: string; page?: number; limit?: number }) =>
  api.get('/publishing/tasks', { params }).then((r) => r.data);
export const retryPublishingTask = (taskId: string) => api.post(`/publishing/tasks/${taskId}/retry`).then((r) => r.data);
export const cancelPublishingTask = (taskId: string) => api.post(`/publishing/tasks/${taskId}/cancel`).then((r) => r.data);
export const reschedulePublishingTask = (taskId: string, scheduledAt: string) =>
  api.patch(`/publishing/tasks/${taskId}/reschedule`, { scheduledAt }).then((r) => r.data);

export const getMediaList = (params?: { page?: number; limit?: number }) =>
  api.get('/media', { params }).then((r) => r.data);
export const uploadMediaFile = (file: File, contentId?: string) => {
  const formData = new FormData();
  formData.append('file', file);
  if (contentId) formData.append('contentId', contentId);
  return api.post('/media/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data);
};
export const deleteMediaItem = (id: string) => api.delete(`/media/${id}`).then((r) => r.data);
