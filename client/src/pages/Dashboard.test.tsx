/**
 * Tests: pages/Dashboard.tsx
 * Kiểm tra render stats, loading state, activity feed, upcoming queue
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { Dashboard } from './Dashboard';

// Mock API client
vi.mock('../api/client', () => ({
  getOverview: vi.fn(),
  getRecentActivity: vi.fn(),
  getUpcomingQueue: vi.fn(),
}));

import * as api from '../api/client';

const mockOverview = {
  connectedAccounts: { total: 3, facebook: 2, youtube: 1, threads: 0, tiktok: 0 },
  content: { total: 10, draft: 2, published: 7, failed: 1 },
  media: { total: 25, totalSizeBytes: 1024 * 1024 * 50 },
  publishing: {
    totalPublished: 42,
    scheduled: 5,
    failed: 2,
    processing: 1,
  },
};

const mockActivity = [
  {
    id: 'task-1',
    platform: 'FACEBOOK',
    status: 'PUBLISHED',
    externalPostUrl: 'https://fb.com/post/1',
    updatedAt: new Date().toISOString(),
    content: { title: 'My FB Post' },
    socialAccount: { accountName: 'My Page' },
  },
  {
    id: 'task-2',
    platform: 'YOUTUBE',
    status: 'FAILED',
    externalPostUrl: null,
    updatedAt: new Date().toISOString(),
    content: { caption: 'My YT Video' },
    socialAccount: { accountName: 'My Channel' },
  },
];

const mockQueue = [
  {
    id: 'task-3',
    platform: 'THREADS',
    status: 'PENDING',
    scheduledAt: '2025-12-31T10:00:00Z',
    content: { title: 'Threads Post' },
    socialAccount: { accountName: 'My Threads' },
  },
];

const renderDashboard = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('Dashboard — loading state', () => {
  beforeEach(() => {
    vi.mocked(api.getOverview).mockReturnValue(new Promise(() => {})); // never resolves
    vi.mocked(api.getRecentActivity).mockReturnValue(new Promise(() => {}));
    vi.mocked(api.getUpcomingQueue).mockReturnValue(new Promise(() => {}));
  });

  it('renders "..." placeholder while loading overview', () => {
    renderDashboard();
    // All 4 stat cards show loading placeholder "..."
    const placeholders = screen.getAllByText('...');
    expect(placeholders.length).toBeGreaterThanOrEqual(4);
  });

  it('renders page title Publisher Dashboard', () => {
    renderDashboard();
    expect(screen.getByText('Publisher Dashboard')).toBeInTheDocument();
  });

  it('renders stat card labels while loading', () => {
    renderDashboard();
    expect(screen.getByText('Connected Accounts')).toBeInTheDocument();
    expect(screen.getByText('Published Posts')).toBeInTheDocument();
    expect(screen.getByText('Scheduled in Queue')).toBeInTheDocument();
    expect(screen.getByText('Failed (Needs Retry)')).toBeInTheDocument();
  });
});

describe('Dashboard — loaded state', () => {
  beforeEach(() => {
    vi.mocked(api.getOverview).mockResolvedValue(mockOverview);
    vi.mocked(api.getRecentActivity).mockResolvedValue(mockActivity);
    vi.mocked(api.getUpcomingQueue).mockResolvedValue(mockQueue);
  });

  it('renders Connected Accounts count = 3', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText('3')).toBeInTheDocument();
    });
  });

  it('renders Published Posts count = 42', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument();
    });
  });

  it('renders Scheduled count = 5', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText('5')).toBeInTheDocument();
    });
  });

  it('renders Failed count = 2', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText('2')).toBeInTheDocument();
    });
  });

  it('renders recent activity post title', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText('My FB Post')).toBeInTheDocument();
    });
  });

  it('renders PUBLISHED status badge in activity', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText('PUBLISHED')).toBeInTheDocument();
    });
  });

  it('renders FAILED status badge in activity', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText('FAILED')).toBeInTheDocument();
    });
  });

  it('renders upcoming queue item title', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText('Threads Post')).toBeInTheDocument();
    });
  });

  it('renders "Open Publishing Calendar" button', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: /open publishing calendar/i })
      ).toBeInTheDocument();
    });
  });
});

describe('Dashboard — empty queue state', () => {
  beforeEach(() => {
    vi.mocked(api.getOverview).mockResolvedValue(mockOverview);
    vi.mocked(api.getRecentActivity).mockResolvedValue([]);
    vi.mocked(api.getUpcomingQueue).mockResolvedValue([]);
  });

  it('shows "No posts currently scheduled" when queue is empty', async () => {
    renderDashboard();
    await waitFor(() => {
      expect(
        screen.getByText(/no posts currently scheduled/i)
      ).toBeInTheDocument();
    });
  });
});

describe('Dashboard — navigation links', () => {
  beforeEach(() => {
    vi.mocked(api.getOverview).mockResolvedValue(mockOverview);
    vi.mocked(api.getRecentActivity).mockResolvedValue([]);
    vi.mocked(api.getUpcomingQueue).mockResolvedValue([]);
  });

  it('Manage Accounts link points to /accounts', async () => {
    renderDashboard();
    await waitFor(() => {
      const link = screen.getByRole('link', { name: /manage accounts/i });
      expect(link).toHaveAttribute('href', '/accounts');
    });
  });

  it('Create New Campaign link points to /create', async () => {
    renderDashboard();
    await waitFor(() => {
      const link = screen.getByRole('link', { name: /create new campaign/i });
      expect(link).toHaveAttribute('href', '/create');
    });
  });
});
