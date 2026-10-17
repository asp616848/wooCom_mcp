import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Input, Label } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { keysApi, type ApiKey } from '@/lib/api';
import { limitLabel, parseLimit } from '@/lib/keys';

/** Admin: set one key's tool-call limit per minute; empty = the gateway default. Applies on the next call. */
export function LimitDialog({
  apiKey,
  fallback,
  onClose,
}: {
  apiKey: ApiKey;
  fallback: number;
  onClose: () => void;
}) {
  const [text, setText] = useState(apiKey.rateLimitPerMin?.toString() ?? '');
  const queryClient = useQueryClient();
  const parsed = parseLimit(text);
  const save = useMutation({
    mutationFn: (value: number | null) => keysApi.setLimit(apiKey.id, value),
    onSuccess: ({ key }) => {
      void queryClient.invalidateQueries({ queryKey: ['keys'] });
      toast.success(`"${key.name}": ${limitLabel(key.rateLimitPerMin, fallback)}`);
      onClose();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if ('value' in parsed) save.mutate(parsed.value);
  }

  return (
    <Dialog open onClose={onClose} title={`Rate limit for "${apiKey.name}"`}>
      <form className="space-y-4" onSubmit={submit}>
        <div className="space-y-1.5">
          <Label htmlFor="key-limit">Tool calls per minute</Label>
          <Input
            id="key-limit"
            inputMode="numeric"
            placeholder={`${fallback} (default)`}
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-invalid={'error' in parsed}
          />
          <p className="text-xs text-zinc-500">
            {'error' in parsed ? (
              <span className="text-red-700">{parsed.error}</span>
            ) : (
              'Leave empty to use the gateway default. Over the limit, calls are rejected until the minute ends.'
            )}
          </p>
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={save.isPending || 'error' in parsed}>
            {save.isPending ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
