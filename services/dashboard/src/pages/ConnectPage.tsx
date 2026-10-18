import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import { CodeBlock, CopyButton, ErrorState, Loading, PageHeader } from '@/components/common';
import { Card, CardContent } from '@/components/ui/card';
import { connectApi } from '@/lib/api';

export function ConnectPage() {
  const info = useQuery({ queryKey: ['connect-info'], queryFn: connectApi.info, staleTime: Infinity });

  return (
    <>
      <PageHeader
        title="Connect a client"
        description="One URL and your API key give any MCP client the tools of every enabled server. No upstream credentials needed."
      />
      {info.isPending && <Loading />}
      {info.isError && <ErrorState error={info.error} />}
      {info.data && (
        <div className="space-y-6">
          <Card>
            <CardContent className="flex flex-wrap items-center gap-3 pt-6">
              <span className="text-sm text-zinc-500">Gateway URL</span>
              <code className="rounded bg-zinc-100 px-2 py-1 font-mono text-sm">{info.data.mcpUrl}</code>
              <CopyButton value={info.data.mcpUrl} />
            </CardContent>
          </Card>
          <p className="text-sm text-zinc-600">
            <strong>Without a key:</strong> clients that support MCP authorization (Claude Code, Inspector)
            sign you in with your browser. Approved apps are listed, and revocable, under{' '}
            <Link to="/keys" className="text-brand hover:underline">
              API keys → Connected apps
            </Link>
            .
          </p>
          <CodeBlock title="Claude Code (browser sign-in)" code={info.data.snippets.claudeCodeOauth} />
          <p className="text-sm text-zinc-600">
            <strong>With a key:</strong> replace <code className="font-mono">&lt;YOUR_API_KEY&gt;</code> with
            a key from{' '}
            <Link to="/keys" className="text-brand hover:underline">
              API keys
            </Link>{' '}
            (creating a key shows these snippets with the key filled in).
          </p>
          <CodeBlock title="Claude Code" code={info.data.snippets.claudeCode} />
          <CodeBlock title="Cursor (.cursor/mcp.json)" code={info.data.snippets.cursor} />
          <CodeBlock title="MCP Inspector" code={info.data.snippets.inspector} />
        </div>
      )}
    </>
  );
}
