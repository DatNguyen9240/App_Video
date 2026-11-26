/**
 * Tests: App.tsx
 * Kiểm tra routing cơ bản và page renders
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// Mock all API calls
vi.mock('./api/client', () => ({
  getSocialAccounts: vi.fn().mockResolvedValue([]),
  getOverview: vi.fn().mockResolvedValue({}),
  getRecentActivity: vi.fn().mockResolvedValue([]),
  getUpcomingQueue: vi.fn().mockResolvedValue([]),
  getMediaList: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getContents: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getPublishingTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getFacebookAuthUrl: vi.fn().mockResolvedValue({ url: '' }),
  getYouTubeAuthUrl: vi.fn().mockResolvedValue({ url: '' }),
  getThreadsAuthUrl: vi.fn().mockResolvedValue({ url: '' }),
  getTikTokAuthUrl: vi.fn().mockResolvedValue({ url: '' }),
  disconnectSocialAccount: vi.fn().mockResolvedValue({ ok: true }),
  refreshSocialAccountToken: vi.fn().mockResolvedValue({ ok: true }),
  connectThreadsManualToken: vi.fn().mockResolvedValue({ ok: true }),
  connectTikTokManualToken: vi.fn().mockResolvedValue({ ok: true }),
  createContent: vi.fn().mockResolvedValue({ id: 'x' }),
  publishNow: vi.fn().mockResolvedValue({ ok: true }),
  schedulePublish: vi.fn().mockResolvedValue({ ok: true }),
  uploadMediaFile: vi.fn().mockResolvedValue({ id: 'm-1' }),
  deleteMediaItem: vi.fn().mockResolvedValue({ ok: true }),
  deleteContent: vi.fn().mockResolvedValue({ ok: true }),
  updateContent: vi.fn().mockResolvedValue({ ok: true }),
  retryPublishingTask: vi.fn().mockResolvedValue({ ok: true }),
  cancelPublishingTask: vi.fn().mockResolvedValue({ ok: true }),
  reschedulePublishingTask: vi.fn().mockResolvedValue({ ok: true }),
  getContentById: vi.fn().mockResolvedValue({}),
}));

import App from './App';

const renderApp = (path = '/') => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('App — routing', () => {
  it('renders without crashing on /', () => {
    renderApp('/');
    expect(document.body).toBeTruthy();
  });

  it('renders AutoPub brand on any page', () => {
    renderApp('/');
    expect(screen.getByText('AutoPub')).toBeInTheDocument();
  });

  it('renders sidebar navigation', () => {
    renderApp('/');
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    expect(screen.getByText('Create Post')).toBeInTheDocument();
  });

  it('renders /create route without crash', () => {
    renderApp('/create');
    expect(screen.getByText('AutoPub')).toBeInTheDocument();
  });

  it('renders /accounts route without crash', () => {
    renderApp('/accounts');
    expect(screen.getByText('AutoPub')).toBeInTheDocument();
  });

  it('renders /media route without crash', () => {
    renderApp('/media');
    expect(screen.getByText('AutoPub')).toBeInTheDocument();
  });

  it('renders /content route without crash', () => {
    renderApp('/content');
    expect(screen.getByText('AutoPub')).toBeInTheDocument();
  });

  it('renders /queue route without crash', () => {
    renderApp('/queue');
    expect(screen.getByText('AutoPub')).toBeInTheDocument();
  });
});
