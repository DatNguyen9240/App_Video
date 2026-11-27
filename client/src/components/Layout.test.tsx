/**
 * Tests: components/Layout.tsx
 * Kiểm tra sidebar navigation, branding, Swagger link
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Layout } from './Layout';

// Outlet cần được mock để tránh lỗi khi render không trong Route context
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom'
  );
  return {
    ...actual,
    Outlet: () => <div data-testid="outlet-content">Page Content</div>,
  };
});

const renderLayout = (initialPath = '/') =>
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Layout />
    </MemoryRouter>
  );

describe('Layout — Sidebar branding', () => {
  it('renders AutoPub brand name', () => {
    renderLayout();
    expect(screen.getByText('AutoPub')).toBeInTheDocument();
  });

  it('renders PRO badge', () => {
    renderLayout();
    expect(screen.getByText('PRO')).toBeInTheDocument();
  });

  it('renders Swagger docs link pointing to localhost:3000', () => {
    renderLayout();
    const link = screen.getByRole('link', { name: /swagger/i });
    expect(link).toHaveAttribute('href', 'http://localhost:3000/api/docs');
  });

  it('renders Sign In link when not logged in', () => {
    renderLayout();
    const link = screen.getByRole('link', { name: /sign in/i });
    expect(link).toHaveAttribute('href', '/login');
  });

  it('renders "Live Official APIs" status badge', () => {
    renderLayout();
    expect(screen.getByText('Live Official APIs')).toBeInTheDocument();
  });
});

describe('Layout — Navigation items', () => {
  const navItems = [
    { name: 'Dashboard', href: '/' },
    { name: 'Create Post', href: '/create' },
    { name: 'Content Library', href: '/content' },
    { name: 'Connected Accounts', href: '/accounts' },
    { name: 'Media Library', href: '/media' },
    { name: 'Publishing Calendar', href: '/calendar' },
    { name: 'Publishing Queue', href: '/queue' },
    { name: 'Publishing History', href: '/history' },
    { name: 'Settings', href: '/settings' },
  ];

  it('renders exactly 9 navigation links', () => {
    renderLayout();
    // Filter to nav links inside sidebar nav (exclude Swagger & New Post header link)
    const navEl = document.querySelector('nav');
    const links = navEl?.querySelectorAll('a');
    expect(links?.length).toBe(9);
  });

  navItems.forEach(({ name, href }) => {
    it(`renders nav item "${name}" with href "${href}"`, () => {
      renderLayout();
      const link = screen.getByRole('link', { name });
      expect(link).toBeInTheDocument();
      expect(link).toHaveAttribute('href', href);
    });
  });
});

describe('Layout — Active state', () => {
  it('active nav item gets bg-indigo-600 class on Dashboard route', () => {
    renderLayout('/');
    const dashLink = screen.getByRole('link', { name: 'Dashboard' });
    expect(dashLink.className).toContain('bg-indigo-600');
  });

  it('inactive nav items do not get bg-indigo-600 class', () => {
    renderLayout('/');
    const createLink = screen.getByRole('link', { name: 'Create Post' });
    expect(createLink.className).not.toContain('bg-indigo-600');
  });

  it('highlights Create Post when on /create route', () => {
    renderLayout('/create');
    const createLink = screen.getByRole('link', { name: 'Create Post' });
    expect(createLink.className).toContain('bg-indigo-600');
  });
});

describe('Layout — Page outlet', () => {
  it('renders outlet (child page content)', () => {
    renderLayout();
    expect(screen.getByTestId('outlet-content')).toBeInTheDocument();
  });

  it('renders New Post button in header', () => {
    renderLayout();
    expect(screen.getByRole('link', { name: /new post/i })).toBeInTheDocument();
  });
});
