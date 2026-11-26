/**
 * Tests: pages/ConnectedAccounts.tsx
 * Kiểm tra danh sách tài khoản, nút kết nối/ngắt kết nối
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  getSocialAccounts: vi.fn(),
  getFacebookAuthUrl: vi.fn(),
  getYouTubeAuthUrl: vi.fn(),
  getThreadsAuthUrl: vi.fn(),
  getTikTokAuthUrl: vi.fn(),
  disconnectSocialAccount: vi.fn(),
  refreshSocialAccountToken: vi.fn(),
  connectThreadsManualToken: vi.fn(),
  connectTikTokManualToken: vi.fn(),
}));

import * as api from '../api/client';
import { ConnectedAccounts } from './ConnectedAccounts';

const mockAccounts = [
  {
    id: 'acc-1',
    platform: 'FACEBOOK',
    accountName: 'My Facebook Page',
    platformAccountId: 'fb-page-001',
    status: 'CONNECTED',
    expiresAt: null,
    createdAt: new Date().toISOString(),
  },
  {
    id: 'acc-2',
    platform: 'YOUTUBE',
    accountName: 'My YouTube Channel',
    platformAccountId: 'yt-ch-001',
    status: 'CONNECTED',
    expiresAt: new Date(Date.now() + 86400000).toISOString(),
    createdAt: new Date().toISOString(),
  },
  {
    id: 'acc-3',
    platform: 'THREADS',
    accountName: 'My Threads Account',
    platformAccountId: 'th-001',
    status: 'TOKEN_EXPIRED',
    expiresAt: new Date(Date.now() - 1000).toISOString(),
    createdAt: new Date().toISOString(),
  },
];

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ConnectedAccounts />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('ConnectedAccounts — loading', () => {
  beforeEach(() => {
    vi.mocked(api.getSocialAccounts).mockReturnValue(new Promise(() => {}));
  });

  it('shows page heading', () => {
    renderPage();
    expect(screen.getByText('Connected Social Accounts')).toBeInTheDocument();
  });
});

describe('ConnectedAccounts — with accounts', () => {
  beforeEach(() => {
    vi.mocked(api.getSocialAccounts).mockResolvedValue(mockAccounts);
    vi.mocked(api.disconnectSocialAccount).mockResolvedValue({ ok: true });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('renders Facebook account name', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('My Facebook Page')).toBeInTheDocument();
    });
  });

  it('renders YouTube account name', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('My YouTube Channel')).toBeInTheDocument();
    });
  });

  it('renders Threads account name', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('My Threads Account')).toBeInTheDocument();
    });
  });

  it('shows account statuses (CONNECTED, TOKEN_EXPIRED)', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByText('CONNECTED').length).toBe(2);
      expect(screen.getByText('TOKEN_EXPIRED')).toBeInTheDocument();
    });
  });

  it('calls disconnectSocialAccount when disconnect button clicked and confirmed', async () => {
    renderPage();
    await waitFor(() => screen.getByText('My Facebook Page'));

    const disconnectBtns = screen.getAllByRole('button', {
      name: /disconnect/i,
    });
    fireEvent.click(disconnectBtns[0]);

    await waitFor(() => {
      expect(api.disconnectSocialAccount).toHaveBeenCalled();
      expect(vi.mocked(api.disconnectSocialAccount).mock.calls[0][0]).toBe('acc-1');
    });
  });
});

describe('ConnectedAccounts — empty state', () => {
  beforeEach(() => {
    vi.mocked(api.getSocialAccounts).mockResolvedValue([]);
  });

  it('shows empty state message when no accounts', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('No social accounts connected')).toBeInTheDocument();
    });
  });
});

describe('ConnectedAccounts — connect new platform', () => {
  beforeEach(() => {
    vi.mocked(api.getSocialAccounts).mockResolvedValue([]);
    vi.mocked(api.getFacebookAuthUrl).mockResolvedValue({
      url: 'https://fb.com/auth',
    });
    vi.mocked(api.getThreadsAuthUrl).mockResolvedValue({
      url: 'https://threads.net/auth',
    });
  });

  it('renders connect buttons for each platform', async () => {
    renderPage();
    await waitFor(() => {
      const connectBtns = screen.getAllByRole('button', { name: /connect/i });
      expect(connectBtns.length).toBeGreaterThanOrEqual(4);
    });
  });
});
