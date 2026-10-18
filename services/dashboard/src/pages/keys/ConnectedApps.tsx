import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { EmptyState, ErrorState, formatTime, Loading } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Table, Td, Th } from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { oauthApi, type ConnectedApp } from '@/lib/api';

/** Apps a user approved with browser sign-in (SO-3): one row per user and app, revocable at once. */
export function ConnectedApps({ all }: { all: boolean }) {
  const queryClient = useQueryClient();
  const apps = useQuery({
    queryKey: ['oauth-apps', all ? 'all' : 'mine'],
    queryFn: () => oauthApi.apps(all),
  });
  const revoke = useMutation({
    mutationFn: (app: ConnectedApp) => oauthApi.revoke(app),
    onSuccess: (_, app) => {
      void queryClient.invalidateQueries({ queryKey: ['oauth-apps'] });
      toast.success(`"${app.clientName}" revoked: it stops working now`);
    },
  });

  return (
    <section className="mt-10">
      <h2 className="text-base font-semibold">Connected apps</h2>
      <p className="mb-3 mt-1 text-sm text-zinc-500">
        Apps you signed in to with your browser instead of an API key. Revoking ends their access at once.
      </p>
      {apps.isPending && <Loading />}
      {apps.isError && <ErrorState error={apps.error} />}
      {apps.data && apps.data.apps.length === 0 && (
        <EmptyState>
          No connected apps. In Claude Code, add the gateway URL without a key and sign in.
        </EmptyState>
      )}
      {apps.data && apps.data.apps.length > 0 && (
        <Table>
          <thead>
            <tr>
              <Th>App</Th>
              {all && <Th>User</Th>}
              <Th>First approved</Th>
              <Th>Last approved</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {apps.data.apps.map((app) => (
              <tr key={`${app.userId}:${app.clientId}`}>
                <Td className="font-medium">{app.clientName}</Td>
                {all && <Td className="text-zinc-600">{app.userEmail}</Td>}
                <Td className="whitespace-nowrap text-zinc-500">{formatTime(app.authorizedAt)}</Td>
                <Td className="whitespace-nowrap text-zinc-500">{formatTime(app.lastAuthorizedAt)}</Td>
                <Td className="text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-700"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(app)}
                  >
                    Revoke
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
