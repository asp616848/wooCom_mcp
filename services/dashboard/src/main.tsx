import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router';

import '@/index.css';
import { Layout } from '@/pages/Layout';
import { LoginPage } from '@/pages/Login';
import { Placeholder } from '@/pages/Placeholder';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/servers" replace /> },
      { path: 'servers', element: <Placeholder title="Servers" phase="P9" /> },
      { path: 'members', element: <Placeholder title="Members" phase="P9" /> },
      { path: 'keys', element: <Placeholder title="API keys" phase="P9" /> },
      { path: 'audit', element: <Placeholder title="Audit log" phase="P9" /> },
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
    </QueryClientProvider>
  </StrictMode>,
);
