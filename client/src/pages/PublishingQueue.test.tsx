/**
 * Tests: pages/PublishingQueue.tsx
 * Kiểm tra danh sách queue, retry, cancel
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  getPublishingTasks: vi.fn(),
  retryPublishingTask: vi.fn(),
  cancelPublishingTask: vi.fn(),
  reschedulePublishingTask: vi.fn(),
}));

import * as api from '../api/client';
import { PublishingQueue } from './PublishingQueue';

const mockTasks = [
  {
    id: 'task-1',
    platform: 'FACEBOOK',
    status: 'PENDING',
    scheduledAt: '2025-12-31T10:00:00Z',
    idempotencyKey: 'idem-key-001',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    content: { title: 'Scheduled FB Post', caption: null },
    socialAccount: { accountName: 'My Page' },
  },
  {
    id: 'task-2',
    platform: 'YOUTUBE',
    status: 'PENDING',
    scheduledAt: null,
    idempotencyKey: 'idem-key-002',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    content: { title: null, caption: 'YT Video Caption' },
    socialAccount: { accountName: 'My Channel' },
  },
];

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PublishingQueue />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('PublishingQueue — render', () => {
  beforeEach(() => {
    vi.mocked(api.getPublishingTasks).mockResolvedValue({
      data: mockTasks,
      total: 2,
      page: 1,
      limit: 30,
    });
    vi.mocked(api.retryPublishingTask).mockResolvedValue({ ok: true });
    vi.mocked(api.cancelPublishingTask).mockResolvedValue({ ok: true });
    vi.mocked(api.reschedulePublishingTask).mockResolvedValue({ ok: true });
  });

  it('renders page heading Publishing Queue', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Publishing Queue')).toBeInTheDocument();
    });
  });

  it('renders PENDING task title', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Scheduled FB Post')).toBeInTheDocument();
    });
  });

  it('renders second task with caption fallback', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('YT Video Caption')).toBeInTheDocument();
    });
  });

  it('renders FACEBOOK platform badge', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('FACEBOOK')).toBeInTheDocument();
    });
  });

  it('renders YOUTUBE platform badge', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('YOUTUBE')).toBeInTheDocument();
    });
  });
});
