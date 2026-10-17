import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { useMe } from '@/auth';
import { EmptyState, formatTime } from '@/components/common';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, Td, Th } from '@/components/ui/table';
import { toast } from '@/components/ui/toast';
import { keysApi, type ApiKey, type KeyList } from '@/lib/api';
import { limitLabel } from '@/lib/keys';
import { LimitDialog } from '@/pages/keys/LimitDialog';

export function KeysTable({ list, showOwner }: { list: KeyList; showOwner?: boolean }) {
  const { keys, defaultRateLimitPerMin: fallback } = list;
  const isAdmin = useMe().data?.role === 'admin';
  const [editing, setEditing] = useState<ApiKey | null>(null);
  const queryClient = useQueryClient();
  const revoke = useMutation({
    mutationFn: (key: ApiKey) => keysApi.revoke(key.id),
    onSuccess: (_, key) => {
      void queryClient.invalidateQueries({ queryKey: ['keys'] });
      toast.success(`Key "${key.name}" revoked: clients using it stop working now`);
    },
  });

  if (keys.length === 0) return <EmptyState>No API keys yet.</EmptyState>;
  return (
    <>
      <Table>
        <thead>
          <tr>
            <Th>Name</Th>
            {showOwner && <Th>Owner</Th>}
            <Th>Prefix</Th>
            <Th>Created</Th>
            <Th>Last used</Th>
            <Th>Rate limit</Th>
            <Th>Status</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key.id}>
              <Td className="font-medium">{key.name}</Td>
              {showOwner && <Td className="text-zinc-600">{key.userEmail}</Td>}
              <Td className="font-mono text-xs">{key.prefix}…</Td>
              <Td className="whitespace-nowrap text-zinc-500">{formatTime(key.createdAt)}</Td>
              <Td className="whitespace-nowrap text-zinc-500">{formatTime(key.lastUsedAt)}</Td>
              <Td className="whitespace-nowrap text-zinc-600">
                {limitLabel(key.rateLimitPerMin, fallback)}
                {isAdmin && !key.revokedAt && fallback > 0 && (
                  <Button variant="ghost" size="sm" className="ml-1" onClick={() => setEditing(key)}>
                    Edit
                  </Button>
                )}
              </Td>
              <Td>
                {key.revokedAt ? <Badge tone="gray">Revoked</Badge> : <Badge tone="green">Active</Badge>}
              </Td>
              <Td className="text-right">
                {!key.revokedAt && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-700"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(key)}
                  >
                    Revoke
                  </Button>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {editing && <LimitDialog apiKey={editing} fallback={fallback} onClose={() => setEditing(null)} />}
    </>
  );
}
