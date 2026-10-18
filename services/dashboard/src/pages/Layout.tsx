import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Boxes,
  ChartColumn,
  KeyRound,
  LogOut,
  MessageSquare,
  Plug,
  ScrollText,
  Server,
  Store,
  Users,
  type LucideIcon,
} from 'lucide-react';
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router';

import { useMe } from '@/auth';
import { Button } from '@/components/ui/button';
import { authApi, type Role } from '@/lib/api';
import { cn } from '@/lib/utils';

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  roles: Role[];
}

// Role-aware nav (DESIGN §11). The API enforces the same rules; hiding links is only convenience.
const NAV: NavItem[] = [
  { to: '/servers', label: 'Servers', icon: Server, roles: ['admin', 'member'] },
  { to: '/members', label: 'Members', icon: Users, roles: ['admin'] },
  { to: '/keys', label: 'API keys', icon: KeyRound, roles: ['admin', 'member'] },
  { to: '/audit', label: 'Audit log', icon: ScrollText, roles: ['admin', 'member'] },
  { to: '/insights', label: 'Insights', icon: ChartColumn, roles: ['admin'] },
  { to: '/connect', label: 'Connect', icon: Plug, roles: ['admin', 'member'] },
  { to: '/chat', label: 'Chat', icon: MessageSquare, roles: ['admin', 'member'] },
  { to: '/stores', label: 'Stores', icon: Store, roles: ['admin', 'member'] },
  { to: '/hosted', label: 'Hosted', icon: Boxes, roles: ['admin'] },
];

export function Layout() {
  const me = useMe();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const logout = useMutation({
    mutationFn: authApi.logout,
    onSettled: () => {
      queryClient.setQueryData(['me'], null);
      navigate('/login', { replace: true });
    },
  });

  if (me.isPending) return <p className="p-8 text-sm text-zinc-500">Loading…</p>;
  if (me.isError) return <p className="p-8 text-sm text-red-600">Could not reach the gateway.</p>;
  if (!me.data)
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  const user = me.data;

  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r border-zinc-200 bg-white p-4">
        <p className="mb-6 px-2 text-sm font-semibold">Woo MCP Gateway</p>
        <nav className="space-y-1">
          {NAV.filter((item) => item.roles.includes(user.role)).map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100',
                  isActive && 'bg-zinc-100 font-medium text-zinc-900',
                )
              }
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      {/* min-w-0: lets wide tables/code scroll inside main instead of widening the whole page */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-end gap-3 border-b border-zinc-200 bg-white px-6 py-3">
          <span className="text-sm">
            {user.name} <span className="text-zinc-500">· {user.email}</span>
          </span>
          <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium capitalize">
            {user.role}
          </span>
          <Button variant="ghost" size="sm" onClick={() => logout.mutate()} disabled={logout.isPending}>
            <LogOut className="size-4" /> Sign out
          </Button>
        </header>
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
