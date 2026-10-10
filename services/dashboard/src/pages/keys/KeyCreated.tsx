import { useQuery } from '@tanstack/react-query';

import { CodeBlock, CopyButton } from '@/components/common';
import { connectApi, KEY_PLACEHOLDER, type CreatedKey } from '@/lib/api';

/** Shown once after creation: the plaintext key plus client snippets with the key already filled in. */
export function KeyCreated({ created, ownerLabel }: { created: CreatedKey; ownerLabel?: string }) {
  const info = useQuery({ queryKey: ['connect-info'], queryFn: connectApi.info, staleTime: Infinity });
  const fill = (snippet: string) => snippet.replaceAll(KEY_PLACEHOLDER, created.key);

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
        Copy this key now{ownerLabel ? ` and send it to ${ownerLabel}` : ''}. It is shown only once; the
        gateway keeps only a hash.
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 overflow-x-auto rounded-md bg-zinc-100 px-3 py-2 font-mono text-xs">
          {created.key}
        </code>
        <CopyButton value={created.key} label="Copy key" />
      </div>
      {info.data && (
        <>
          <p className="text-sm text-zinc-600">
            Gateway URL: <code className="font-mono text-xs">{info.data.mcpUrl}</code>
          </p>
          <CodeBlock title="Claude Code" code={fill(info.data.snippets.claudeCode)} />
          <CodeBlock title="Cursor (.cursor/mcp.json)" code={fill(info.data.snippets.cursor)} />
          <CodeBlock title="MCP Inspector" code={fill(info.data.snippets.inspector)} />
        </>
      )}
    </div>
  );
}
