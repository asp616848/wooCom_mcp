import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { useMe } from '@/auth';
import { ErrorState, Loading, PageHeader } from '@/components/common';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Input, Label } from '@/components/ui/input';
import { keysApi, type CreatedKey } from '@/lib/api';
import { KeyCreated } from '@/pages/keys/KeyCreated';
import { KeysTable } from '@/pages/keys/KeysTable';

export function KeysPage() {
  const isAdmin = useMe().data?.role === 'admin';
  const [allUsers, setAllUsers] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [created, setCreated] = useState<CreatedKey | null>(null);
  const queryClient = useQueryClient();
  const keys = useQuery({
    queryKey: ['keys', allUsers ? 'all' : 'mine'],
    queryFn: () => keysApi.list({ all: allUsers }),
  });
  const create = useMutation({
    mutationFn: () => keysApi.create(name.trim()),
    onSuccess: (key) => {
      void queryClient.invalidateQueries({ queryKey: ['keys'] });
      setCreated(key);
      setName('');
    },
  });

  function close() {
    setCreating(false);
    setCreated(null);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <>
      <PageHeader
        title="API keys"
        description="Use a key with the gateway URL in any MCP client. Keys act as you: same role, same audit trail."
        actions={
          <>
            {isAdmin && (
              <Button variant="outline" onClick={() => setAllUsers((v) => !v)}>
                {allUsers ? 'Show my keys' : 'Show all users'}
              </Button>
            )}
            <Button onClick={() => setCreating(true)}>
              <Plus className="size-4" /> Create key
            </Button>
          </>
        }
      />
      {keys.isPending && <Loading />}
      {keys.isError && <ErrorState error={keys.error} />}
      {keys.data && <KeysTable keys={keys.data.keys} showOwner={allUsers} />}

      <Dialog
        open={creating}
        onClose={close}
        title={created ? 'Key created' : 'Create API key'}
        className="max-w-2xl"
      >
        {created ? (
          <>
            <KeyCreated created={created} />
            <div className="mt-4 flex justify-end">
              <Button onClick={close}>Done</Button>
            </div>
          </>
        ) : (
          <form className="space-y-4" onSubmit={submit}>
            <div className="space-y-1.5">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                required
                maxLength={50}
                placeholder="e.g. Claude Code on my laptop"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? 'Creating…' : 'Create key'}
            </Button>
          </form>
        )}
      </Dialog>
    </>
  );
}
