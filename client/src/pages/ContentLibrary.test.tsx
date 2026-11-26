/**
 * Tests: pages/ContentLibrary.tsx
 * Kiểm tra danh sách content, filter, pagination
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  getContents: vi.fn(),
  deleteContent: vi.fn(),
  publishNow: vi.fn(),
}));

import * as api from '../api/client';
import { ContentLibrary } from './ContentLibrary';

const mockContents = [
  {
    id: 'c-1',
    title: 'First Post',
    caption: 'Hello world',
    status: 'DRAFT',
    targetPlatforms: ['FACEBOOK'],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  },
  {
    id: 'c-2',
    title: 'Second Post',
    caption: 'Second caption',
    status: 'PUBLISHED',
    targetPlatforms: ['THREADS', 'TIKTOK'],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  },
  {
    id: 'c-3',
    title: null,
    caption: 'Caption only post',
    status: 'FAILED',
    targetPlatforms: ['YOUTUBE'],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  },
];

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ContentLibrary />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('ContentLibrary — render', () => {
  beforeEach(() => {
    vi.mocked(api.getContents).mockResolvedValue({
      data: mockContents,
      total: 3,
      page: 1,
      limit: 20,
    });
  });

  it('renders page heading Content Library', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Content Library')).toBeInTheDocument();
    });
  });

  it('renders first post title', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('First Post')).toBeInTheDocument();
    });
  });

  it('renders DRAFT status badge', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('DRAFT')).toBeInTheDocument();
    });
  });

  it('renders PUBLISHED status badge', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('PUBLISHED')).toBeInTheDocument();
    });
  });

  it('renders FAILED status badge', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('FAILED')).toBeInTheDocument();
    });
  });
});

describe('ContentLibrary — empty state', () => {
  beforeEach(() => {
    vi.mocked(api.getContents).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      limit: 20,
    });
  });

  it('shows no content when empty', async () => {
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByText(/no content posts match your filters/i)
      ).toBeInTheDocument();
    });
  });
});
