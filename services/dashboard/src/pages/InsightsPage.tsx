import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Navigate } from 'react-router';

import { useMe } from '@/auth';
import { EmptyState, ErrorState, formatTime, Loading, PageHeader } from '@/components/common';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Select } from '@/components/ui/select';
import { Table, Td, Th } from '@/components/ui/table';
import { insightsApi, type InsightsWindow } from '@/lib/api';

const WINDOWS: { value: InsightsWindow; label: string }[] = [
  { value: '1h', label: 'Last hour' },
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
];

export const percent = (rate: number) => `${(rate * 100).toFixed(rate > 0 && rate < 0.01 ? 2 : 1)}%`;

/** Error rates above 5% are worth a look, above 20% something is broken. */
export function rateTone(rate: number): 'green' | 'amber' | 'red' {
  if (rate > 0.2) return 'red';
  if (rate > 0.05) return 'amber';
  return 'green';
}

/** SO-8: tool-call health from the audit log (admins). Refreshes every 15 s. */
export function InsightsPage() {
  const me = useMe().data;
  const [range, setRange] = useState<InsightsWindow>('24h');
  const insights = useQuery({
    queryKey: ['insights', range],
    queryFn: () => insightsApi.get(range),
    refetchInterval: 15000,
    enabled: me?.role === 'admin',
  });

  if (me?.role !== 'admin') return <Navigate to="/servers" replace />;
  const data = insights.data;

  return (
    <>
      <PageHeader
        title="Insights"
        description="Tool calls through the gateway, from the audit log. Raw counters are at the gateway's /metrics."
        actions={
          <Select
            aria-label="Time window"
            value={range}
            onChange={(e) => setRange(e.target.value as InsightsWindow)}
          >
            {WINDOWS.map((w) => (
              <option key={w.value} value={w.value}>
                {w.label}
              </option>
            ))}
          </Select>
        }
      />
      {insights.isPending && <Loading />}
      {insights.isError && <ErrorState error={insights.error} />}
      {data && data.calls === 0 && <EmptyState>No tool calls in this window yet.</EmptyState>}
      {data && data.calls > 0 && (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-3">
            <Stat label="Tool calls" value={data.calls.toLocaleString()} />
            <Stat label="Error rate" value={percent(data.errorRate)} tone={rateTone(data.errorRate)} />
            <Stat label="Servers called" value={String(data.servers.length)} />
          </div>

          <h2 className="mb-3 text-base font-semibold">By server</h2>
          <div className="mb-6">
            <Table>
              <thead>
                <tr>
                  <Th>Server</Th>
                  <Th className="text-right">Calls</Th>
                  <Th className="text-right">Error rate</Th>
                  <Th className="text-right">p50</Th>
                  <Th className="text-right">p95</Th>
                  <Th>Outcomes</Th>
                </tr>
              </thead>
              <tbody>
                {data.servers.map((s) => (
                  <tr key={s.serverSlug}>
                    <Td className="font-mono text-xs">{s.serverSlug}</Td>
                    <Td className="text-right tabular-nums">{s.calls}</Td>
                    <Td className="text-right">
                      <Badge tone={rateTone(s.errorRate)}>{percent(s.errorRate)}</Badge>
                    </Td>
                    <Td className="text-right tabular-nums">{s.p50Ms} ms</Td>
                    <Td className="text-right tabular-nums">{s.p95Ms} ms</Td>
                    <Td className="text-xs text-zinc-600">
                      {Object.entries(s.outcomes)
                        .sort(([, a], [, b]) => (b ?? 0) - (a ?? 0))
                        .map(([outcome, n]) => `${outcome} ${n}`)
                        .join(' · ')}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <section>
              <h2 className="mb-3 text-base font-semibold">Busiest tools</h2>
              <Table>
                <thead>
                  <tr>
                    <Th>Tool</Th>
                    <Th className="text-right">Calls</Th>
                    <Th className="text-right">Errors</Th>
                    <Th className="text-right">p95</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.tools.map((t) => (
                    <tr key={t.exposedName}>
                      <Td className="font-mono text-xs break-all">{t.exposedName}</Td>
                      <Td className="text-right tabular-nums">{t.calls}</Td>
                      <Td className="text-right">{percent(t.errorRate)}</Td>
                      <Td className="text-right tabular-nums">{t.p95Ms} ms</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </section>
            <section>
              <h2 className="mb-3 text-base font-semibold">Top failure reasons</h2>
              {data.failures.length === 0 ? (
                <EmptyState>No failures in this window.</EmptyState>
              ) : (
                <Table>
                  <thead>
                    <tr>
                      <Th>Reason</Th>
                      <Th className="text-right">Count</Th>
                      <Th>Last seen</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.failures.map((f) => (
                      <tr key={`${f.serverSlug}|${f.outcome}|${f.message}`}>
                        <Td>
                          <div className="flex flex-wrap items-center gap-1">
                            <span className="font-mono text-xs">{f.serverSlug}</span>
                            <Badge tone={f.outcome === 'tool_error' ? 'amber' : 'red'}>{f.outcome}</Badge>
                          </div>
                          <p className="mt-1 text-xs break-words text-zinc-600">{f.message}</p>
                        </Td>
                        <Td className="text-right tabular-nums">{f.count}</Td>
                        <Td className="text-xs whitespace-nowrap">{formatTime(f.lastSeen)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </section>
          </div>
        </>
      )}
    </>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'green' | 'amber' | 'red' }) {
  const color = { green: 'text-green-700', amber: 'text-amber-700', red: 'text-red-700' }[tone ?? 'green'];
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-xs text-zinc-500">{label}</div>
        <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone ? color : ''}`}>{value}</div>
      </CardContent>
    </Card>
  );
}
