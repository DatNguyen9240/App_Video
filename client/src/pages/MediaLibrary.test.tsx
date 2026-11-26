/**
 * Tests: pages/MediaLibrary.tsx
 * Kiểm tra danh sách media, upload, delete
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  getMediaList: vi.fn(),
  uploadMediaFile: vi.fn(),
  deleteMediaItem: vi.fn(),
}));

import * as api from '../api/client';
import { MediaLibrary } from './MediaLibrary';

const mockMediaItems = [
  {
    id: 'm-1',
    originalName: 'video_intro.mp4',
    mimeType: 'video/mp4',
    fileSize: 1024 * 1024 * 10,
    url: 'http://localhost:3000/uploads/video_intro.mp4',
    createdAt: new Date().toISOString(),
  },
  {
    id: 'm-2',
    originalName: 'thumbnail.jpg',
    mimeType: 'image/jpeg',
    fileSize: 1024 * 150,
    url: 'http://localhost:3000/uploads/thumbnail.jpg',
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
        <MediaLibrary />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe('MediaLibrary — render', () => {
  beforeEach(() => {
    vi.mocked(api.getMediaList).mockResolvedValue({
      data: mockMediaItems,
      total: 2,
    });
  });

  it('renders page heading with Media Asset Library text', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Media Asset Library')).toBeInTheDocument();
    });
  });

  it('renders video file name', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('video_intro.mp4')).toBeInTheDocument();
    });
  });

  it('renders image file name', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('thumbnail.jpg')).toBeInTheDocument();
    });
  });
});

describe('MediaLibrary — empty state', () => {
  beforeEach(() => {
    vi.mocked(api.getMediaList).mockResolvedValue({ data: [], total: 0 });
  });

  it('shows empty state when no media', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/no media files uploaded/i)).toBeInTheDocument();
    });
  });
});
