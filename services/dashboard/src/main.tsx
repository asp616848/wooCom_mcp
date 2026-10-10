import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router';

import '@/index.css';
import { errorMessage } from '@/components/common';
import { toast, Toaster } from '@/components/ui/toast';
import { ApiError } from '@/lib/api';
import { AuditPage } from '@/pages/AuditPage';
import { ConnectPage } from '@/pages/ConnectPage';
import { InvitePage } from '@/pages/InvitePage';
import { KeysPage } from '@/pages/keys/KeysPage';
import { Layout } from '@/pages/Layout';
import { LoginPage } from '@/pages/Login';
import { MembersPage } from '@/pages/MembersPage';
import { Placeholder } from '@/pages/Placeholder';
import { NewServerPage } from '@/pages/servers/NewServerPage';
import { ServerDetailPage } from '@/pages/servers/ServerDetailPage';
import { ServersPage } from '@/pages/servers/ServersPage';

// DESIGN §11: a 401 anywhere means the session is gone → `me` becomes null and the layout redirects to /login.
// Failed mutations show a toast unless the component shows the error itself (`meta: { silent: true }`).
function onError(error: unknown) {
  if (error instanceof ApiError && error.status === 401) queryClient.setQueryData(['me'], null);
}

const queryClient: QueryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
  queryCache: new QueryCache({ onError }),
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      onError(error);
      if (!mutation.meta?.silent) toast.error(errorMessage(error));
    },
  }),
});

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/invite/:token', element: <InvitePage /> },
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/servers" replace /> },
      { path: 'servers', element: <ServersPage /> },
      { path: 'servers/new', element: <NewServerPage /> },
      { path: 'servers/:id', element: <ServerDetailPage /> },
      { path: 'members', element: <MembersPage /> },
      { path: 'keys', element: <KeysPage /> },
      { path: 'audit', element: <AuditPage /> },
      { path: 'connect', element: <ConnectPage /> },
      { path: 'chat', element: <Placeholder title="Chat" phase="P10" /> },
      { path: '*', element: <Navigate to="/servers" replace /> },
    ],
  },
]);

const root = document.getElementById('root');
if (!root) throw new Error('#root element missing from index.html');

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>
  </StrictMode>,
);
