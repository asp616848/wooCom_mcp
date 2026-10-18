import { useMutation, useQuery } from '@tanstack/react-query';
import { Navigate, useLocation } from 'react-router';

import { useMe } from '@/auth';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, oauthApi } from '@/lib/api';
import { consentRequestId, safeAppRedirect } from '@/lib/oauth';

/** The consent step of the MCP authorization flow (SO-3, D98): an app asked to use the gateway as this user. */
export function OAuthConsentPage() {
  const me = useMe();
  const location = useLocation();
  const id = consentRequestId(location.search);
  const request = useQuery({
    queryKey: ['oauth-request', id],
    queryFn: () => oauthApi.request(id ?? ''),
    enabled: id !== null && !!me.data,
    retry: false,
    meta: { silent: true },
  });
  const answer = useMutation({
    mutationFn: (approve: boolean) => (approve ? oauthApi.approve(id ?? '') : oauthApi.deny(id ?? '')),
    meta: { silent: true },
    onSuccess: ({ redirectUrl }) => {
      const target = safeAppRedirect(redirectUrl);
      if (target) window.location.assign(target);
    },
  });

  if (me.isPending) return <Shell>Loading…</Shell>;
  if (!me.data) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }
  if (id === null)
    return <Shell>This link is not a valid authorization request. Start again from the app.</Shell>;
  if (request.isPending) return <Shell>Loading…</Shell>;
  if (request.isError) {
    const gone = request.error instanceof ApiError && request.error.status === 404;
    return (
      <Shell>
        {gone
          ? 'This request has expired or was already answered. Start again from the app.'
          : 'Could not load this request. Try again.'}
      </Shell>
    );
  }
  if (answer.isSuccess) {
    const target = safeAppRedirect(answer.data.redirectUrl);
    return (
      <Shell>
        {target
          ? 'Done. You can close this tab and go back to the app.'
          : 'The app gave a redirect address that this gateway will not open. Nothing was shared with it.'}
      </Shell>
    );
  }

  const { clientName, redirectHost, scopes } = request.data.request;
  const user = me.data;
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Authorize {clientName}?</CardTitle>
          <CardDescription>
            This app wants to use the gateway&apos;s tools as <strong>{user.name}</strong> ({user.email}).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <dl className="space-y-2">
            <div className="flex justify-between gap-4">
              <dt className="text-zinc-500">App</dt>
              <dd className="font-medium">{clientName}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-zinc-500">Returns you to</dt>
              <dd className="break-all font-mono text-xs">{redirectHost}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-zinc-500">Access</dt>
              <dd className="flex gap-1">
                {scopes.map((scope) => (
                  <Badge key={scope}>{scope}</Badge>
                ))}
              </dd>
            </div>
          </dl>
          <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900">
            Approve only if you just started connecting this app. It will have your permissions ({user.role})
            and every call it makes is recorded under your name. You can revoke it any time under{' '}
            <em>API keys → Connected apps</em>.
          </p>
          {answer.isError && (
            <p role="alert" className="text-red-700">
              {answer.error instanceof ApiError
                ? answer.error.message
                : 'Something went wrong. Start again from the app.'}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" disabled={answer.isPending} onClick={() => answer.mutate(false)}>
              Deny
            </Button>
            <Button disabled={answer.isPending} onClick={() => answer.mutate(true)}>
              {answer.isPending ? 'Working…' : 'Approve'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}

function Shell({ children }: { children: string }) {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <p className="max-w-md text-center text-sm text-zinc-600">{children}</p>
    </main>
  );
}
