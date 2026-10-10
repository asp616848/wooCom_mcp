import { useQueryClient } from '@tanstack/react-query';
import { CircleAlert, CircleCheck, Loader2, RotateCcw, Send, Wrench } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';

import { useMe } from '@/auth';
import { errorMessage, PageHeader } from '@/components/common';
import { Markdown } from '@/components/markdown';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api';
import { MAX_CONTENT, streamChat, type ChatEvent, type ChatMessage } from '@/lib/chat';

interface ToolCard {
  id: string;
  name: string;
  arguments: unknown;
  result?: { ok: boolean; durationMs: number; preview: string };
}

type Turn =
  | { role: 'user'; content: string }
  | { role: 'assistant'; tools: ToolCard[]; content: string; error: string | null; done: boolean };

const EXAMPLES = [
  'What orders came in today?',
  'Create a product named "Brass Desk Lamp" priced 1299',
  'What is 18% GST on 2500 rupees?',
  'Can you deliver to pincode 560001?',
];

/** The history the API sees: user messages and the assistant's answers (tool traces stay client-side). */
function history(turns: Turn[]): ChatMessage[] {
  return turns.flatMap((t): ChatMessage[] => {
    if (t.role === 'user') return [{ role: 'user', content: t.content }];
    return t.content ? [{ role: 'assistant', content: t.content.slice(0, MAX_CONTENT) }] : [];
  });
}

function apply(turn: Extract<Turn, { role: 'assistant' }>, event: ChatEvent): typeof turn {
  switch (event.type) {
    case 'tool_call':
      return { ...turn, tools: [...turn.tools, { ...event.data }] };
    case 'tool_result':
      return {
        ...turn,
        tools: turn.tools.map((t) =>
          t.id === event.data.id
            ? {
                ...t,
                result: { ok: event.data.ok, durationMs: event.data.durationMs, preview: event.data.preview },
              }
            : t,
        ),
      };
    case 'answer':
      return { ...turn, content: event.data.text };
    case 'error':
      return { ...turn, error: event.data.message };
    case 'done':
      return { ...turn, done: true };
  }
}

export function ChatPage() {
  const me = useMe().data;
  const queryClient = useQueryClient();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);

  useEffect(() => () => abort.current?.abort(), []);
  // Block body: an effect's return value is its cleanup, and scrollIntoView() returns a Promise in newer browsers.
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' });
  }, [turns]);

  function updateLast(change: (turn: Extract<Turn, { role: 'assistant' }>) => Turn) {
    setTurns((prev) => {
      const last = prev[prev.length - 1];
      return last?.role === 'assistant' ? [...prev.slice(0, -1), change(last)] : prev;
    });
  }

  async function send(text: string) {
    const content = text.trim();
    if (!content || streaming) return;
    const next: Turn[] = [...turns, { role: 'user', content }];
    setTurns([...next, { role: 'assistant', tools: [], content: '', error: null, done: false }]);
    setInput('');
    setStreaming(true);
    const controller = new AbortController();
    abort.current = controller;
    try {
      await streamChat(history(next), (event) => updateLast((turn) => apply(turn, event)), controller.signal);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) queryClient.setQueryData(['me'], null);
      updateLast((turn) => ({ ...turn, error: errorMessage(error) }));
    } finally {
      updateLast((turn) => ({ ...turn, done: true }));
      if (abort.current === controller) {
        abort.current = null;
        setStreaming(false);
      }
    }
  }

  function newChat() {
    abort.current?.abort();
    abort.current = null;
    setStreaming(false);
    setTurns([]);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(input);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send(input);
    }
  }

  return (
    <div className="flex h-[calc(100dvh-7rem)] max-w-3xl flex-col">
      <PageHeader
        title="Chat"
        description={`The assistant uses the gateway's tools as ${me?.name ?? 'you'}, with your permissions. Every tool call is audited.`}
        actions={
          turns.length > 0 && (
            <Button variant="outline" onClick={newChat}>
              <RotateCcw className="size-4" /> New chat
            </Button>
          )
        }
      />

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pb-4" aria-live="polite">
        {turns.length === 0 && (
          <div className="space-y-3 rounded-xl border border-dashed border-zinc-300 bg-white p-6">
            <p className="text-sm text-zinc-600">
              Ask about orders, products, GST or delivery. Pick an example to edit it, then press Send:
            </p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((example) => (
                <Button
                  key={example}
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    // fill, don't send: some examples change the store, so the user reviews and presses Send
                    setInput(example);
                    textarea.current?.focus();
                  }}
                >
                  {example}
                </Button>
              ))}
            </div>
          </div>
        )}
        {turns.map((turn, i) =>
          turn.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl bg-brand px-4 py-2 text-sm whitespace-pre-wrap text-white">
                {turn.content}
              </div>
            </div>
          ) : (
            <AssistantTurn key={i} turn={turn} />
          ),
        )}
        <div ref={bottom} />
      </div>

      <form onSubmit={onSubmit} className="flex items-end gap-2 border-t border-zinc-200 pt-4">
        <textarea
          ref={textarea}
          aria-label="Message"
          className="max-h-40 min-h-10 flex-1 resize-y rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20 disabled:bg-zinc-50"
          placeholder={
            streaming ? 'Working…' : 'Message the assistant (Enter to send, Shift+Enter for a new line)'
          }
          rows={2}
          maxLength={MAX_CONTENT}
          disabled={streaming}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <Button type="submit" disabled={streaming || input.trim() === ''}>
          {streaming ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          Send
        </Button>
      </form>
    </div>
  );
}

function AssistantTurn({ turn }: { turn: Extract<Turn, { role: 'assistant' }> }) {
  const thinking = !turn.done && !turn.content && !turn.error;
  return (
    <div className="max-w-[85%] space-y-2">
      {turn.tools.map((tool) => (
        <ToolCallCard key={tool.id} tool={tool} />
      ))}
      {thinking && (
        <p className="flex items-center gap-2 text-sm text-zinc-500">
          <Loader2 className="size-4 animate-spin" /> {turn.tools.length ? 'Working…' : 'Thinking…'}
        </p>
      )}
      {turn.content && (
        <div className="rounded-2xl border border-zinc-200 bg-white px-4 py-2 text-sm break-words">
          <Markdown>{turn.content}</Markdown>
        </div>
      )}
      {turn.error && (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700"
        >
          {turn.error}
        </div>
      )}
    </div>
  );
}

/** DESIGN §11: collapsible card with the exposed name, args JSON, ok/error, duration and the result preview. */
function ToolCallCard({ tool }: { tool: ToolCard }) {
  const args = typeof tool.arguments === 'string' ? tool.arguments : JSON.stringify(tool.arguments, null, 2);
  return (
    <details className="group rounded-lg border border-zinc-200 bg-zinc-50 text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
        <Wrench className="size-3.5 text-zinc-500" />
        <code className="font-mono font-medium">{tool.name}</code>
        {!tool.result && <Loader2 className="size-3.5 animate-spin text-zinc-400" />}
        {tool.result && (
          <>
            <Badge tone={tool.result.ok ? 'green' : 'red'}>
              {tool.result.ok ? <CircleCheck className="size-3" /> : <CircleAlert className="size-3" />}
              {tool.result.ok ? 'ok' : 'error'}
            </Badge>
            <span className="text-zinc-500">{tool.result.durationMs} ms</span>
          </>
        )}
        <span className="ml-auto text-zinc-400 group-open:hidden">details</span>
      </summary>
      <div className="space-y-2 border-t border-zinc-200 px-3 py-2">
        <div>
          <div className="mb-1 font-medium text-zinc-500">Arguments</div>
          <pre className="overflow-x-auto rounded bg-white p-2 font-mono">{args}</pre>
        </div>
        {tool.result && (
          <div>
            <div className="mb-1 font-medium text-zinc-500">Result (first 500 characters)</div>
            <pre className="max-h-48 overflow-auto rounded bg-white p-2 font-mono whitespace-pre-wrap">
              {tool.result.preview || '(empty)'}
            </pre>
          </div>
        )}
      </div>
    </details>
  );
}
