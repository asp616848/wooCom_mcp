import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './api';
import { parseSse, streamChat, type ChatEvent } from './chat';

afterEach(() => vi.unstubAllGlobals());

describe('parseSse', () => {
  it('parses complete events, skips keep-alives and keeps the unfinished tail', () => {
    const { events, rest } = parseSse(
      ': keep-alive\n\nevent: tool_call\ndata: {"id":"c1","name":"gst__calculate_gst","arguments":{}}\n\nevent: answer\ndata: {"te',
    );
    expect(events).toEqual([
      { type: 'tool_call', data: { id: 'c1', name: 'gst__calculate_gst', arguments: {} } },
    ]);
    expect(rest).toBe('event: answer\ndata: {"te');
    expect(parseSse(rest + 'xt":"ok"}\n\n').events).toEqual([{ type: 'answer', data: { text: 'ok' } }]);
  });
});

describe('streamChat', () => {
  it('posts the history with the CSRF header and emits events across chunk boundaries', async () => {
    const chunks = [
      'event: answer\ndata: {"text":',
      '"hi"}\n\nevent: done\ndata: {"llmCalls":1,"toolCalls":0}\n\n',
    ];
    const body = new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
        controller.close();
      },
    });
    const fetch = vi.fn(async () => new Response(body, { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    const events: ChatEvent[] = [];
    const history = Array.from({ length: 25 }, (_, i) => ({ role: 'user' as const, content: `m${i}` }));
    await streamChat(history, (e) => events.push(e));

    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/chat');
    expect(init.headers).toMatchObject({ 'X-WMG-CSRF': '1' });
    const sent = JSON.parse(init.body as string) as { messages: { content: string }[] };
    expect(sent.messages).toHaveLength(20);
    expect(sent.messages.at(-1)?.content).toBe('m24');
    expect(events.map((e) => e.type)).toEqual(['answer', 'done']);
  });

  it('turns an error envelope into ApiError', async () => {
    const error = { error: { code: 'LLM_NOT_CONFIGURED', message: 'Chat is not configured' } };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(error), { status: 503 })),
    );
    await expect(streamChat([{ role: 'user', content: 'hi' }], () => {})).rejects.toEqual(
      new ApiError(503, 'LLM_NOT_CONFIGURED', 'Chat is not configured'),
    );
  });
});
