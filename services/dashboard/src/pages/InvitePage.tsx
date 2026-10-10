import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useNavigate, useParams } from 'react-router';

import { errorMessage } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input, Label } from '@/components/ui/input';
import { invitesApi } from '@/lib/api';

/** Public page behind an invite link: set your name and password, then you're signed in. */
export function InvitePage() {
  const { token = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const info = useQuery({ queryKey: ['invite', token], queryFn: () => invitesApi.info(token), retry: false });
  const accept = useMutation({
    mutationFn: () => invitesApi.accept(token, name.trim(), password),
    meta: { silent: true },
    onSuccess: ({ user }) => {
      queryClient.setQueryData(['me'], user);
      navigate('/connect', { replace: true });
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    accept.mutate();
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Join Woo MCP Gateway</CardTitle>
          {info.data?.valid && (
            <CardDescription>
              {info.data.email} is invited as {info.data.role === 'admin' ? 'an admin' : 'a member'}.
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          {info.isPending && <p className="text-sm text-zinc-500">Checking the invite…</p>}
          {(info.isError || info.data?.valid === false) && (
            <p role="alert" className="text-sm text-red-600">
              This invite link is invalid, already used or expired. Ask an admin for a new one.
            </p>
          )}
          {info.data?.valid && (
            <form className="space-y-4" onSubmit={submit}>
              <div className="space-y-1.5">
                <Label htmlFor="name">Your name</Label>
                <Input
                  id="name"
                  required
                  maxLength={80}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <p className="text-xs text-zinc-500">At least 12 characters.</p>
              </div>
              {accept.error && (
                <p role="alert" className="text-sm text-red-600">
                  {errorMessage(accept.error)}
                </p>
              )}
              <Button type="submit" className="w-full" disabled={accept.isPending}>
                {accept.isPending ? 'Creating account…' : 'Create account'}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
