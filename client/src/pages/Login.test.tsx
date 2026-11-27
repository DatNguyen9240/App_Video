import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  loginUser: vi.fn(),
  registerUser: vi.fn(),
}));

import * as api from '../api/client';
import { Login } from './Login';

describe('Login page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('renders Sign In form by default', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );
    expect(screen.getByText('AutoPub Workspace')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('you@example.com')).toBeInTheDocument();
  });

  it('switches to Register mode and shows Display Name field', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );
    fireEvent.click(screen.getByRole('button', { name: 'Register' }));
    expect(screen.getByPlaceholderText('Your workspace name')).toBeInTheDocument();
  });

  it('calls loginUser and stores JWT token on submit', async () => {
    vi.mocked(api.loginUser).mockResolvedValue({
      accessToken: 'jwt-abc-123',
      user: { id: 'u-1', email: 'test@example.com', name: 'Test' },
    });

    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), {
      target: { value: 'test@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), {
      target: { value: 'secret123' },
    });
    const signInButtons = screen.getAllByRole('button', { name: /sign in/i });
    fireEvent.click(signInButtons[signInButtons.length - 1]);

    await waitFor(() => {
      expect(api.loginUser).toHaveBeenCalledWith({
        email: 'test@example.com',
        password: 'secret123',
      });
      expect(localStorage.getItem('autopub_token')).toBe('jwt-abc-123');
    });
  });
});
