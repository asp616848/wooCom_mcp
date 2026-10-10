import { Check, Copy } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ApiError, type ServerStatus } from '@/lib/api';

const STATUS: Record<ServerStatus, { label: string; tone: 'green' | 'red' | 'gray' | 'amber' }> = {
  connected: { label: 'Connected', tone: 'green' },
  unreachable: { label: 'Unreachable', tone: 'red' },
  disabled: { label: 'Disabled', tone: 'gray' },
  unknown: { label: 'Checking…', tone: 'amber' },
};

/** DESIGN §11: Connected green, Unreachable red (tooltip = last error), Disabled gray. */
export function StatusBadge({ status, error }: { status: ServerStatus; error?: string | null }) {
  const { label, tone } = STATUS[status];
  return (
    <Badge tone={tone} title={status === 'unreachable' && error ? error : undefined}>
      <span className="size-1.5 rounded-full bg-current" />
      {label}
    </Badge>
  );
}

export function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
      {copied ? 'Copied' : label}
    </Button>
  );
}

export function CodeBlock({ code, title }: { code: string; title?: string }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        {title && <span className="text-xs font-medium text-zinc-500">{title}</span>}
        <CopyButton value={code} />
      </div>
      <pre className="overflow-x-auto rounded-md bg-zinc-900 p-3 text-xs leading-relaxed text-zinc-100">
        <code>{code}</code>
      </pre>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {description && <p className="mt-1 text-sm text-zinc-500">{description}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-zinc-300 bg-white p-8 text-center text-sm text-zinc-500">
      {children}
    </div>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof ApiError ? error.message : 'Something went wrong. Try again.';
  return (
    <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      {message}
    </div>
  );
}

export function Loading() {
  return <p className="text-sm text-zinc-500">Loading…</p>;
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Request failed';
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
