import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, UserPlus } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Navigate } from 'react-router';

import { useMe } from '@/auth';
import { CopyButton, EmptyState, ErrorState, formatTime, Loading, PageHeader } from '@/components/common';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Input, Label } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Table, Td, Th } from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { invitesApi, keysApi, usersApi, type CreatedKey, type Role, type User } from '@/lib/api';
import { KeyCreated } from '@/pages/keys/KeyCreated';
import { KeysTable } from '@/pages/keys/KeysTable';

export function MembersPage() {
  const me = useMe().data;
  const users = useQuery({ queryKey: ['users'], queryFn: usersApi.list });
  const invites = useQuery({ queryKey: ['invites'], queryFn: invitesApi.list });
  const [inviting, setInviting] = useState(false);
  const [keysFor, setKeysFor] = useState<User | null>(null);
  const queryClient = useQueryClient();

  const update = useMutation({
    mutationFn: ({ user, body }: { user: User; body: { role?: Role; status?: User['status'] } }) =>
      usersApi.update(user.id, body),
    onSuccess: ({ user }) => {
      void queryClient.invalidateQueries({ queryKey: ['users'] });
      toast.success(
        `${user.name} is now ${user.status === 'disabled' ? 'disabled' : `an active ${user.role}`}`,
      );
    },
  });
  const revokeInvite = useMutation({
    mutationFn: (id: string) => invitesApi.revoke(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['invites'] });
      toast.success('Invite revoked');
    },
  });

  if (me?.role !== 'admin') return <Navigate to="/servers" replace />;

  return (
    <>
      <PageHeader
        title="Members"
        description="Invite teammates, set their role, and manage their API keys."
        actions={
          <Button onClick={() => setInviting(true)}>
            <UserPlus className="size-4" /> Invite member
          </Button>
        }
      />
      {users.isPending && <Loading />}
      {users.isError && <ErrorState error={users.error} />}
      {users.data && (
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Role</Th>
              <Th>Status</Th>
              <Th>Last sign-in</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {users.data.users.map((user) => {
              const self = user.id === me.id;
              return (
                <tr key={user.id}>
                  <Td>
                    <div className="font-medium">
                      {user.name} {self && <span className="text-xs text-zinc-500">(you)</span>}
                    </div>
                    <div className="text-xs text-zinc-500">{user.email}</div>
                  </Td>
                  <Td>
                    <Select
                      aria-label={`Role of ${user.name}`}
                      value={user.role}
                      disabled={self || update.isPending}
                      onChange={(e) => update.mutate({ user, body: { role: e.target.value as Role } })}
                    >
                      <option value="member">Member</option>
                      <option value="admin">Admin</option>
                    </Select>
                  </Td>
                  <Td>
                    {user.status === 'active' ? <Badge tone="green">Active</Badge> : <Badge>Disabled</Badge>}
                  </Td>
                  <Td className="whitespace-nowrap text-zinc-500">{formatTime(user.lastLoginAt)}</Td>
                  <Td className="space-x-1 text-right whitespace-nowrap">
                    <Button variant="ghost" size="sm" onClick={() => setKeysFor(user)}>
                      <KeyRound className="size-4" /> Keys
                    </Button>
                    {!self && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={update.isPending}
                        onClick={() =>
                          update.mutate({
                            user,
                            body: { status: user.status === 'active' ? 'disabled' : 'active' },
                          })
                        }
                      >
                        {user.status === 'active' ? 'Disable' : 'Enable'}
                      </Button>
                    )}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}

      <h2 className="mt-8 mb-3 text-base font-semibold">Pending invites</h2>
      {invites.data && invites.data.invites.length === 0 && <EmptyState>No pending invites.</EmptyState>}
      {invites.data && invites.data.invites.length > 0 && (
        <Table>
          <thead>
            <tr>
              <Th>Email</Th>
              <Th>Role</Th>
              <Th>Expires</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {invites.data.invites.map((invite) => (
              <tr key={invite.id}>
                <Td>{invite.email}</Td>
                <Td className="capitalize">{invite.role}</Td>
                <Td className="whitespace-nowrap text-zinc-500">{formatTime(invite.expiresAt)}</Td>
                <Td className="text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-700"
                    onClick={() => revokeInvite.mutate(invite.id)}
                  >
                    Revoke
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <InviteDialog open={inviting} onClose={() => setInviting(false)} />
      {keysFor && <UserKeysDialog user={keysFor} onClose={() => setKeysFor(null)} />}
    </>
  );
}

function InviteDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('member');
  const [link, setLink] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () => invitesApi.create(email.trim(), role),
    onSuccess: ({ inviteUrl }) => {
      void queryClient.invalidateQueries({ queryKey: ['invites'] });
      setLink(inviteUrl);
    },
  });

  function close() {
    setEmail('');
    setRole('member');
    setLink(null);
    onClose();
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Dialog open={open} onClose={close} title={link ? 'Invite link' : 'Invite a member'}>
      {link ? (
        <div className="space-y-4">
          <p className="text-sm text-zinc-600">
            Send this link to {email}. It works once and expires in 72 hours. It is shown only now.
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded-md bg-zinc-100 px-3 py-2 font-mono text-xs">
              {link}
            </code>
            <CopyButton value={link} />
          </div>
          <div className="flex justify-end">
            <Button onClick={close}>Done</Button>
          </div>
        </div>
      ) : (
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-1.5">
            <Label htmlFor="invite-email">Email</Label>
            <Input
              id="invite-email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="invite-role">Role</Label>
            <Select id="invite-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
              <option value="member">Member: uses tools, manages own keys</option>
              <option value="admin">Admin: also manages servers and members</option>
            </Select>
          </div>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? 'Creating…' : 'Create invite link'}
          </Button>
        </form>
      )}
    </Dialog>
  );
}

function UserKeysDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const [created, setCreated] = useState<CreatedKey | null>(null);
  const queryClient = useQueryClient();
  const keys = useQuery({
    queryKey: ['keys', 'user', user.id],
    queryFn: () => keysApi.list({ userId: user.id }),
  });
  const issue = useMutation({
    mutationFn: () => keysApi.create(`Issued by admin for ${user.name}`.slice(0, 50), user.id),
    onSuccess: (key) => {
      void queryClient.invalidateQueries({ queryKey: ['keys'] });
      setCreated(key);
    },
  });

  return (
    <Dialog open onClose={onClose} title={`API keys of ${user.name}`} className="max-w-3xl">
      {created ? (
        <>
          <KeyCreated created={created} ownerLabel={user.name} />
          <div className="mt-4 flex justify-end">
            <Button onClick={() => setCreated(null)}>Back to keys</Button>
          </div>
        </>
      ) : (
        <div className="space-y-4">
          {keys.isPending && <Loading />}
          {keys.isError && <ErrorState error={keys.error} />}
          {keys.data && <KeysTable list={keys.data} />}
          <Button disabled={issue.isPending || user.status !== 'active'} onClick={() => issue.mutate()}>
            <KeyRound className="size-4" /> Issue a key for {user.name}
          </Button>
        </div>
      )}
    </Dialog>
  );
}
