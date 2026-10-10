import ReactMarkdown, { type Components } from 'react-markdown';

/**
 * Renders the chat agent's Markdown answers (CommonMark). Raw HTML in the text is never rendered (react-markdown's
 * default), so a model reply that echoes tool data can't inject markup; links open in a new tab without a referrer.
 * Element styles come from descendant selectors on the wrapper (no typography plugin).
 */
const STYLES = [
  '[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0',
  '[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5',
  '[&_h1]:mt-3 [&_h1]:mb-1 [&_h1]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1 [&_h2]:font-semibold',
  '[&_h3]:mt-3 [&_h3]:mb-1 [&_h3]:font-semibold',
  '[&_code]:rounded [&_code]:bg-zinc-100 [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs',
  '[&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-zinc-100 [&_pre]:p-2 [&_pre_code]:p-0',
  '[&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-zinc-300 [&_blockquote]:pl-3',
].join(' ');

const components: Components = {
  a: ({ href, children }) => (
    <a href={href} className="text-brand underline" target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className={STYLES}>
      <ReactMarkdown components={components}>{children}</ReactMarkdown>
    </div>
  );
}
