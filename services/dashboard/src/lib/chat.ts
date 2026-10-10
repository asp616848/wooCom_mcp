/**
 * POST /api/chat client (DESIGN §12.1). The response is Server-Sent Events over a POST, so EventSource (GET only)
 * can't be used: the body is read with a stream reader and split into `event:`/`data:` blocks.
 */
import { ApiError } from '@/lib/api';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export type ChatEvent =
  | { type: 'tool_call'; data: { id: string; name: string; arguments: unknown } }
  | {
      type: 'tool_result';
      data: { id: string; name: string; ok: boolean; durationMs: number; preview: string };
    }
  | { type: 'answer'; data: { text: string } }
  | { type: 'error'; data: { message: string } }
  | { type: 'done'; data: { llmCalls: number; toolCalls: number } };

export const MAX_MESSAGES = 20;
export const MAX_CONTENT = 8000;

/** Split a buffer into complete SSE events; `rest` is an unfinished block to prepend to the next chunk. */
export function parseSse(buffer: string): { events: ChatEvent[]; rest: string } {
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n');
  const rest = blocks.pop() ?? '';
  const events: ChatEvent[] = [];
  for (const block of blocks) {
    let type = '';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue; // keep-alive comment
      if (line.startsWith('event:')) type = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (type && data.length > 0) {
      events.push({ type, data: JSON.parse(data.join('\n')) } as ChatEvent);
    }
  }
  return { events, rest };
}

/** Streams one turn; calls `onEvent` per event. Rejects with ApiError when the request itself fails. */
export async function streamChat(
  messages: ChatMessage[],
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    credentials: 'include',
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json', 'X-WMG-CSRF': '1' },
    body: JSON.stringify({ messages: messages.slice(-MAX_MESSAGES) }),
    signal,
  });
  if (!response.ok || !response.body) {
    const data: unknown = await response.json().catch(() => null);
    const error = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? 'HTTP_ERROR',
      error?.message ?? `Request failed (${response.status})`,
    );
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseSse(buffer + value);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
}
