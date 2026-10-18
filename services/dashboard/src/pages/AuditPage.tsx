import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Fragment, useState } from 'react';

import { useMe } from '@/auth';
import { EmptyState, ErrorState, Loading, PageHeader } from '@/components/common';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import { Table, Td, Th } from '@/components/ui/table';
import { auditApi, serversApi, usersApi, type AuditFilters, type Outcome } from '@/lib/api';

const PAGE = 50;
const OUTCOMES: { value: Outcome; tone: 'green' | 'red' | 'amber' | 'gray' }[] = [
  { value: 'success', tone: 'green' },
  { value: 'tool_error', tone: 'amber' },
  { value: 'upstream_error', tone: 'red' },
  { value: 'timeout', tone: 'red' },
  { value: 'denied', tone: 'red' },
  { value: 'unknown_tool', tone: 'gray' },
  { value: 'rate_limited', tone: 'amber' },
];
const TONE = Object.fromEntries(OUTCOMES.map((o) => [o.value, o.tone])) as Record<
  Outcome,
  (typeof OUTCOMES)[0]['tone']
>;

export function AuditPage() {
  const isAdmin = useMe().data?.role === 'admin';
  const [filters, setFilters] = useState<AuditFilters>({});
  const [live, setLive] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const servers = useQuery({ queryKey: ['servers'], queryFn: serversApi.list });
  const users = useQuery({ queryKey: ['users'], queryFn: usersApi.list, enabled: isAdmin });

  const audit = useInfiniteQuery({
    queryKey: ['audit', filters],
    queryFn: ({ pageParam }) => auditApi.list({ ...filters, cursor: pageParam, limit: PAGE }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    refetchInterval: live ? 5000 : false,
  });
  const items = audit.data?.pages.flatMap((p) => p.items) ?? [];
  const set = (patch: AuditFilters) => setFilters((f) => ({ ...f, ...patch }));

  return (
    <>
      <PageHeader
        title="Audit log"
        description={
          isAdmin
            ? 'Every tool call through the gateway: who, which server and tool, when, how long, and the outcome.'
            : 'Your tool calls through the gateway.'
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {isAdmin && (
          <Select
            aria-label="Filter by user"
            value={filters.userId ?? ''}
            onChange={(e) => set({ userId: e.target.value || undefined })}
          >
            <option value="">All users</option>
            {users.data?.users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.email}
              </option>
            ))}
          </Select>
        )}
        <Select
          aria-label="Filter by server"
          value={filters.serverSlug ?? ''}
          onChange={(e) => set({ serverSlug: e.target.value || undefined })}
        >
          <option value="">All servers</option>
          {servers.data?.servers.map((s) => (
            <option key={s.id} value={s.slug}>
              {s.slug}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by outcome"
          value={filters.outcome ?? ''}
          onChange={(e) => set({ outcome: (e.target.value || undefined) as Outcome | undefined })}
        >
          <option value="">All outcomes</option>
          {OUTCOMES.map((o) => (
            <option key={o.value} value={o.value}>
              {o.value}
            </option>
          ))}
        </Select>
        <label className="ml-auto flex items-center gap-2 text-sm">
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
          Live (refresh every 5 s)
        </label>
      </div>

      {audit.isPending && <Loading />}
      {audit.isError && <ErrorState error={audit.error} />}
      {audit.data && items.length === 0 && <EmptyState>No tool calls match these filters yet.</EmptyState>}
      {items.length > 0 && (
        <Table>
          <thead>
            <tr>
              <Th>Time</Th>
              {isAdmin && <Th>User</Th>}
              <Th>Server · tool</Th>
              <Th>Outcome</Th>
              <Th className="text-right">Duration</Th>
              <Th>Client</Th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <Fragment key={item.id}>
                <tr
                  className="cursor-pointer hover:bg-zinc-50"
                  onClick={() => setOpen(open === item.id ? null : item.id)}
                >
                  <Td className="whitespace-nowrap text-zinc-500">{new Date(item.ts).toLocaleString()}</Td>
                  {isAdmin && (
                    <Td>
                      {item.userEmail ?? '—'}
                      {item.credentialType === 'chat_agent' && <Badge className="ml-1">chat</Badge>}
                      {item.credentialType === 'oauth' && <Badge className="ml-1">oauth</Badge>}
                    </Td>
                  )}
                  <Td className="font-mono text-xs">
                    {item.serverSlug
                      ? `${item.serverSlug} · ${item.toolName}`
                      : (item.exposedName ?? item.method)}
                  </Td>
                  <Td>
                    <Badge tone={TONE[item.outcome]}>{item.outcome}</Badge>
                  </Td>
                  <Td className="text-right tabular-nums">{item.durationMs} ms</Td>
                  <Td className="max-w-40 truncate text-zinc-500">{item.clientName ?? '—'}</Td>
                </tr>
                {open === item.id && (
                  <tr>
                    <Td colSpan={isAdmin ? 6 : 5} className="bg-zinc-50">
                      {item.errorMessage && <p className="mb-2 text-sm text-red-700">{item.errorMessage}</p>}
                      <pre className="overflow-x-auto text-xs">
                        {JSON.stringify(
                          { arguments: item.argsSummary, method: item.method, requestId: item.requestId },
                          null,
                          2,
                        )}
                      </pre>
                    </Td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      )}
      {audit.hasNextPage && (
        <div className="mt-4 flex justify-center">
          <Button
            variant="outline"
            disabled={audit.isFetchingNextPage}
            onClick={() => void audit.fetchNextPage()}
          >
            {audit.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </Button>
        </div>
      )}
    </>
  );
}
