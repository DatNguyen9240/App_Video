/**
 * Tests: api/client.ts
 * Kiểm tra axios instance, default headers, và các hàm API
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { mockInstance, mockCreate } = vi.hoisted(() => {
  const inst = {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    defaults: { baseURL: '/api', headers: { common: {} } },
  };
  return {
    mockInstance: inst,
    mockCreate: vi.fn(() => inst),
  };
});

vi.mock('axios', () => ({
  default: {
    create: mockCreate,
  },
}));

import * as apiClient from './client';

describe('API Client — axios instance', () => {
  it('creates axios instance with baseURL /api', () => {
    expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ baseURL: '/api' })
    );
  });

  it('sets x-user-id demo header', () => {
    expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        headers: expect.objectContaining({
          'x-user-id': 'demo-user-id-00000000-0000-0000',
        }),
      })
    );
  });

  it('sets Content-Type application/json header', () => {
    expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
        }),
      })
    );
  });
});

describe('Dashboard API functions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockInstance.get.mockResolvedValue({ data: { ok: true } });
  });

  it('getOverview calls GET /dashboard/overview', async () => {
    await apiClient.getOverview();
    expect(mockInstance.get).toHaveBeenCalledWith('/dashboard/overview');
  });

  it('getRecentActivity calls GET with limit param', async () => {
    await apiClient.getRecentActivity(5);
    expect(mockInstance.get).toHaveBeenCalledWith(
      '/dashboard/recent-activity?limit=5'
    );
  });

  it('getRecentActivity defaults to limit=10', async () => {
    await apiClient.getRecentActivity();
    expect(mockInstance.get).toHaveBeenCalledWith(
      '/dashboard/recent-activity?limit=10'
    );
  });

  it('getUpcomingQueue calls GET with limit param', async () => {
    await apiClient.getUpcomingQueue(3);
    expect(mockInstance.get).toHaveBeenCalledWith(
      '/dashboard/upcoming-queue?limit=3'
    );
  });
});

describe('Social Accounts API functions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockInstance.get.mockResolvedValue({ data: [] });
    mockInstance.post.mockResolvedValue({ data: { ok: true } });
    mockInstance.delete.mockResolvedValue({ data: { ok: true } });
  });

  it('getSocialAccounts calls GET /social-accounts', async () => {
    await apiClient.getSocialAccounts();
    expect(mockInstance.get).toHaveBeenCalledWith('/social-accounts');
  });

  it('getFacebookAuthUrl calls GET /social-accounts/facebook/connect', async () => {
    await apiClient.getFacebookAuthUrl();
    expect(mockInstance.get).toHaveBeenCalledWith(
      '/social-accounts/facebook/connect'
    );
  });

  it('getThreadsAuthUrl calls GET /social-accounts/threads/connect', async () => {
    await apiClient.getThreadsAuthUrl();
    expect(mockInstance.get).toHaveBeenCalledWith(
      '/social-accounts/threads/connect'
    );
  });

  it('getTikTokAuthUrl calls GET /social-accounts/tiktok/connect', async () => {
    await apiClient.getTikTokAuthUrl();
    expect(mockInstance.get).toHaveBeenCalledWith(
      '/social-accounts/tiktok/connect'
    );
  });

  it('connectThreadsManualToken posts token', async () => {
    await apiClient.connectThreadsManualToken('my-token');
    expect(mockInstance.post).toHaveBeenCalledWith(
      '/social-accounts/threads/manual-token',
      { token: 'my-token' }
    );
  });

  it('connectTikTokManualToken posts token', async () => {
    await apiClient.connectTikTokManualToken('tiktok-token-xyz');
    expect(mockInstance.post).toHaveBeenCalledWith(
      '/social-accounts/tiktok/manual-token',
      { token: 'tiktok-token-xyz' }
    );
  });

  it('disconnectSocialAccount calls DELETE /social-accounts/:id', async () => {
    await apiClient.disconnectSocialAccount('acc-123');
    expect(mockInstance.delete).toHaveBeenCalledWith('/social-accounts/acc-123');
  });
});

describe('Content API functions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockInstance.get.mockResolvedValue({ data: { data: [], total: 0 } });
    mockInstance.post.mockResolvedValue({ data: { id: 'c-1' } });
    mockInstance.patch.mockResolvedValue({ data: { id: 'c-1' } });
    mockInstance.delete.mockResolvedValue({ data: { ok: true } });
  });

  it('getContents calls GET /contents with params', async () => {
    await apiClient.getContents({ status: 'DRAFT', page: 2, limit: 20 });
    expect(mockInstance.get).toHaveBeenCalledWith('/contents', {
      params: { status: 'DRAFT', page: 2, limit: 20 },
    });
  });

  it('getContentById calls GET /contents/:id', async () => {
    await apiClient.getContentById('c-999');
    expect(mockInstance.get).toHaveBeenCalledWith('/contents/c-999');
  });

  it('createContent calls POST /contents', async () => {
    const payload = { title: 'Test', caption: 'Hello', platform: 'THREADS' };
    await apiClient.createContent(payload);
    expect(mockInstance.post).toHaveBeenCalledWith('/contents', payload);
  });

  it('updateContent calls PATCH /contents/:id', async () => {
    await apiClient.updateContent('c-1', { title: 'Updated' });
    expect(mockInstance.patch).toHaveBeenCalledWith('/contents/c-1', {
      title: 'Updated',
    });
  });

  it('deleteContent calls DELETE /contents/:id', async () => {
    await apiClient.deleteContent('c-42');
    expect(mockInstance.delete).toHaveBeenCalledWith('/contents/c-42');
  });
});

describe('Publishing API functions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockInstance.post.mockResolvedValue({ data: { ok: true } });
    mockInstance.get.mockResolvedValue({ data: { data: [] } });
    mockInstance.patch.mockResolvedValue({ data: { ok: true } });
  });

  it('publishNow posts contentId to /publishing/publish-now', async () => {
    await apiClient.publishNow('content-99');
    expect(mockInstance.post).toHaveBeenCalledWith('/publishing/publish-now', {
      contentId: 'content-99',
    });
  });

  it('schedulePublish posts contentId + scheduledAt', async () => {
    await apiClient.schedulePublish('content-1', '2025-01-01T12:00:00Z');
    expect(mockInstance.post).toHaveBeenCalledWith('/publishing/schedule', {
      contentId: 'content-1',
      scheduledAt: '2025-01-01T12:00:00Z',
    });
  });

  it('retryPublishingTask posts to /publishing/tasks/:id/retry', async () => {
    await apiClient.retryPublishingTask('task-77');
    expect(mockInstance.post).toHaveBeenCalledWith(
      '/publishing/tasks/task-77/retry'
    );
  });

  it('cancelPublishingTask posts to /publishing/tasks/:id/cancel', async () => {
    await apiClient.cancelPublishingTask('task-77');
    expect(mockInstance.post).toHaveBeenCalledWith(
      '/publishing/tasks/task-77/cancel'
    );
  });
});

describe('Media API functions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockInstance.get.mockResolvedValue({ data: { data: [] } });
    mockInstance.post.mockResolvedValue({ data: { id: 'm-1' } });
    mockInstance.delete.mockResolvedValue({ data: { ok: true } });
  });

  it('getMediaList calls GET /media', async () => {
    await apiClient.getMediaList({ page: 1, limit: 10 });
    expect(mockInstance.get).toHaveBeenCalledWith('/media', {
      params: { page: 1, limit: 10 },
    });
  });

  it('deleteMediaItem calls DELETE /media/:id', async () => {
    await apiClient.deleteMediaItem('m-999');
    expect(mockInstance.delete).toHaveBeenCalledWith('/media/m-999');
  });

  it('uploadMediaFile sends FormData with multipart header', async () => {
    const fakeFile = new File(['hello'], 'test.mp4', { type: 'video/mp4' });
    await apiClient.uploadMediaFile(fakeFile, 'c-1');
    expect(mockInstance.post).toHaveBeenCalledWith(
      '/media/upload',
      expect.any(FormData),
      { headers: { 'Content-Type': 'multipart/form-data' } }
    );
  });

  it('uploadMediaFile works without contentId', async () => {
    const fakeFile = new File(['data'], 'img.jpg', { type: 'image/jpeg' });
    await apiClient.uploadMediaFile(fakeFile);
    expect(mockInstance.post).toHaveBeenCalledWith(
      '/media/upload',
      expect.any(FormData),
      { headers: { 'Content-Type': 'multipart/form-data' } }
    );
  });
});
