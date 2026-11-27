import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  getPlatformConfigs: vi.fn(),
  savePlatformConfig: vi.fn(),
  deletePlatformConfig: vi.fn(),
}));

import * as api from '../api/client';
import { Settings } from './Settings';

describe('Settings page (DB Platform Configs)', () => {
  beforeEach(() => {
    vi.mocked(api.getPlatformConfigs).mockResolvedValue([
      {
        platform: 'FACEBOOK',
        appId: 'fb-db-id',
        redirectUri: 'http://localhost:3000/fb/cb',
        hasSecret: true,
        isActive: true,
        source: 'DATABASE',
      },
      {
        platform: 'TIKTOK',
        appId: 'tt-env-id',
        redirectUri: 'http://localhost:3000/tt/cb',
        hasSecret: true,
        isActive: true,
        source: 'ENV',
      },
    ]);
  });

  it('renders DB Platform OAuth App Credentials section with Database and From .env badges', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <Settings />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => {
      expect(screen.getByText('Platform OAuth App Credentials (Database)')).toBeInTheDocument();
      expect(screen.getByText('Database')).toBeInTheDocument();
      expect(screen.getByText('From .env')).toBeInTheDocument();
    });
  });
});
