import { CheckCircle2, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';

import { cn } from '@/lib/utils';

type Tone = 'success' | 'error';
interface ToastItem {
  id: number;
  tone: Tone;
  message: string;
}

// Module-level store so non-React code (the query/mutation caches) can raise toasts too.
let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<(items: ToastItem[]) => void>();

function emit() {
  for (const listener of listeners) listener(items);
}

function push(tone: Tone, message: string) {
  const id = nextId++;
  items = [...items, { id, tone, message }];
  emit();
  setTimeout(() => {
    items = items.filter((t) => t.id !== id);
    emit();
  }, 4000);
}

export const toast = {
  success: (message: string) => push('success', message),
  error: (message: string) => push('error', message),
};

/** Bottom-right toasts, auto-dismissed after 4 s, announced to screen readers. */
export function Toaster() {
  const [current, setCurrent] = useState<ToastItem[]>(items);
  useEffect(() => {
    listeners.add(setCurrent);
    return () => {
      listeners.delete(setCurrent);
    };
  }, []);

  return (
    <div aria-live="polite" className="fixed right-4 bottom-4 z-50 flex w-80 flex-col gap-2">
      {current.map((t) => (
        <div
          key={t.id}
          role="status"
          className={cn(
            'flex items-start gap-2 rounded-lg border bg-white p-3 text-sm shadow-lg',
            t.tone === 'error' ? 'border-red-200' : 'border-emerald-200',
          )}
        >
          {t.tone === 'error' ? (
            <XCircle className="mt-0.5 size-4 shrink-0 text-red-600" />
          ) : (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
          )}
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  );
}
