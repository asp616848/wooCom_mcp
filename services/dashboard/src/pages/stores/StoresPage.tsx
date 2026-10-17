import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Plus, RotateCw, Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { useMe } from '@/auth';
import { EmptyState, ErrorState, formatTime, Loading, PageHeader } from '@/components/common';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Input, Label } from '@/components/ui/input';
import { Table, Td, Th } from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { storesApi, type Store, type StoreStatus } from '@/lib/api';
import { storeNameProblem } from '@/lib/stores';

const STATUS: Record<StoreStatus, { label: string; tone: 'green' | 'red' | 'gray' | 'amber' }> = {
  provisioning: { label: 'Provisioning', tone: 'amber' },
  ready: { label: 'Ready', tone: 'green' },
  failed: { label: 'Failed', tone: 'red' },
  deleting: { label: 'Deleting', tone: 'gray' },
};

function StoreStatusBadge({ store }: { store: Store }) {
  const { label, tone } = STATUS[store.status];
  const detail = store.status === 'provisioning' || store.status === 'deleting' ? store.statusReason : null;
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone={tone}>
        <span className="size-1.5 rounded-full bg-current" />
        {label}
      </Badge>
      {detail && <span className="text-xs text-zinc-500">{detail}</span>}
    </span>
  );
}

/** Bonus (DESIGN §13, §11): create/delete/retry stores; the list polls every 3 s while anything is in flight. */
export function StoresPage() {
  const isAdmin = useMe().data?.role === 'admin';
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [deleting, setDeleting] = useState<Store | null>(null);
  const stores = useQuery({ queryKey: ['stores'], queryFn: storesApi.list, refetchInterval: 3000 });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['stores'] });

  const create = useMutation({
    mutationFn: () => storesApi.create(name),
    meta: { silent: true }, // the dialog shows the error
    onSuccess: ({ store }) => {
      refresh();
      setCreating(false);
      setName('');
      toast.success(`Store "${store.name}" is being provisioned (usually 3–6 minutes)`);
    },
  });
  const retry = useMutation({
    mutationFn: (store: Store) => storesApi.retry(store.name),
    onSuccess: ({ store }) => {
      refresh();
      toast.success(`Retrying "${store.name}" (attempt ${store.attempt})`);
    },
  });
  const remove = useMutation({
    mutationFn: (store: Store) => storesApi.remove(store.name),
    onSuccess: ({ store }) => {
      refresh();
      setDeleting(null);
      toast.success(`Deleting "${store.name}": its tools are removed first, then its namespace and data`);
    },
  });

  const problem = storeNameProblem(name);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!problem) create.mutate();
  }

  function closeCreate() {
    setCreating(false);
    setName('');
    create.reset();
  }

  return (
    <>
      <PageHeader
        title="Stores"
        description="Each store is its own WooCommerce in its own namespace, with its own MCP server registered as woo-<name>."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="size-4" /> Create store
          </Button>
        }
      />
      {stores.isPending && <Loading />}
      {stores.isError && <ErrorState error={stores.error} />}
      {stores.data &&
        (stores.data.stores.length === 0 ? (
          <EmptyState>No stores yet. The main store is managed by the platform itself.</EmptyState>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                {isAdmin && <Th>Owner</Th>}
                <Th>Status</Th>
                <Th>URL</Th>
                <Th>Tools</Th>
                <Th>Created</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {stores.data.stores.map((store) => (
                <tr key={store.name} className="align-top">
                  <Td className="font-medium">{store.name}</Td>
                  {isAdmin && <Td className="text-zinc-600">{store.owner}</Td>}
                  <Td>
                    <StoreStatusBadge store={store} />
                    {store.status === 'failed' && store.statusReason && (
                      <pre className="mt-2 max-w-md overflow-x-auto rounded bg-red-50 p-2 text-xs whitespace-pre-wrap text-red-800">
                        {store.statusReason}
                      </pre>
                    )}
                  </Td>
                  <Td>
                    {store.status === 'ready' ? (
                      <a
                        href={store.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-blue-700 hover:underline"
                      >
                        {store.url.replace(/^https?:\/\//, '')} <ExternalLink className="size-3" />
                      </a>
                    ) : (
                      <span className="text-zinc-400">{store.url.replace(/^https?:\/\//, '')}</span>
                    )}
                  </Td>
                  <Td className="font-mono text-xs">{store.toolsPrefix}*</Td>
                  <Td className="whitespace-nowrap text-zinc-500">{formatTime(store.createdAt)}</Td>
                  <Td className="text-right whitespace-nowrap">
                    {store.status === 'failed' &&
                      !store.statusReason?.match(/^(teardown failed|could not start teardown)/) && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={retry.isPending}
                          onClick={() => retry.mutate(store)}
                        >
                          <RotateCw className="size-4" /> Retry
                        </Button>
                      )}
                    {store.status !== 'deleting' && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-700"
                        onClick={() => setDeleting(store)}
                      >
                        <Trash2 className="size-4" /> Delete
                      </Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ))}

      <Dialog open={creating} onClose={closeCreate} title="Create store">
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-1.5">
            <Label htmlFor="store-name">Name</Label>
            <Input
              id="store-name"
              required
              autoFocus
              maxLength={16}
              placeholder="e.g. paris"
              value={name}
              onChange={(e) => {
                setName(e.target.value.trim().toLowerCase());
                create.reset();
              }}
            />
            <p className="text-xs text-zinc-500">
              {problem ??
                (name ? `Shop and tools: ${name}.shop… · woo-${name}__*` : 'Lowercase letters and digits.')}
            </p>
          </div>
          {create.isError && <ErrorState error={create.error} />}
          <Button type="submit" disabled={create.isPending || !name || Boolean(problem)}>
            {create.isPending ? 'Creating…' : 'Create store'}
          </Button>
        </form>
      </Dialog>

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`Delete store "${deleting?.name}"?`}
      >
        <p className="text-sm text-zinc-600">
          Its MCP tools disappear right away; then its namespace, database, files and secrets are removed.
          This cannot be undone.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={() => setDeleting(null)}>
            Cancel
          </Button>
          <Button
            className="bg-red-700 hover:bg-red-800"
            disabled={remove.isPending}
            onClick={() => deleting && remove.mutate(deleting)}
          >
            {remove.isPending ? 'Deleting…' : 'Delete store'}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
