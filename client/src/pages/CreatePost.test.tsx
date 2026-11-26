/**
 * Tests: pages/CreatePost.tsx
 * Kiểm tra form tạo post: platform selection, validation, submit
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  getSocialAccounts: vi.fn(),
  getMediaList: vi.fn(),
  createContent: vi.fn(),
  publishNow: vi.fn(),
  schedulePublish: vi.fn(),
  uploadMediaFile: vi.fn(),
}));

import * as api from '../api/client';
import { CreatePost } from './CreatePost';

const mockAccounts = [
  {
    id: 'acc-1',
    platform: 'FACEBOOK',
    accountName: 'FB Page',
    avatarUrl: '',
    status: 'CONNECTED',
  },
  {
    id: 'acc-2',
    platform: 'THREADS',
    accountName: 'Threads Acct',
    avatarUrl: '',
    status: 'CONNECTED',
  },
];

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CreatePost />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('CreatePost — initial render', () => {
  beforeEach(() => {
    vi.mocked(api.getSocialAccounts).mockResolvedValue(mockAccounts);
    vi.mocked(api.createContent).mockResolvedValue({ id: 'new-content-1' });
  });

  it('renders page heading', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Create & Schedule Post')).toBeInTheDocument();
    });
  });

  it('renders caption/text input fields', async () => {
    renderPage();
    await waitFor(() => {
      const inputs = screen.getAllByRole('textbox');
      expect(inputs.length).toBeGreaterThan(0);
    });
  });
});

describe('CreatePost — platform selection', () => {
  beforeEach(() => {
    vi.mocked(api.getSocialAccounts).mockResolvedValue(mockAccounts);
  });

  it('renders default selected platform account (FB Page) and Threads after toggle', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByText('FB Page').length).toBeGreaterThan(0);
    });

    // Click Threads platform toggle button to include THREADS
    const threadsBtn = screen.getByRole('button', { name: /threads/i });
    fireEvent.click(threadsBtn);

    await waitFor(() => {
      expect(screen.getAllByText('Threads Acct').length).toBeGreaterThan(0);
    });
  });

  it('shows no target accounts when empty', async () => {
    vi.mocked(api.getSocialAccounts).mockResolvedValue([]);
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText('FB Page')).not.toBeInTheDocument();
    });
  });
});

describe('CreatePost — form submission', () => {
  beforeEach(() => {
    vi.mocked(api.getSocialAccounts).mockResolvedValue(mockAccounts);
    vi.mocked(api.createContent).mockResolvedValue({ id: 'created-1' });
  });

  it('updates text input value on change', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByText('FB Page').length).toBeGreaterThan(0);
    });

    const textboxes = screen.getAllByRole('textbox');
    expect(textboxes.length).toBeGreaterThan(0);
    fireEvent.change(textboxes[0], { target: { value: 'Test post content' } });
    expect(textboxes[0]).toHaveValue('Test post content');
  });
});
