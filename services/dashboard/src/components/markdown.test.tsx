import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Markdown } from './markdown';

describe('Markdown', () => {
  it('renders bold, lists and inline code', () => {
    const html = renderToStaticMarkup(
      <Markdown>{'**Order #20**\n\n* one\n* `woo-main__list_orders`'}</Markdown>,
    );
    expect(html).toContain('<strong>Order #20</strong>');
    expect(html).toContain('<li>one</li>');
    expect(html).toContain('woo-main__list_orders</code>');
  });

  it('never renders raw HTML from the model', () => {
    const html = renderToStaticMarkup(
      <Markdown>{'hi <img src=x onerror=alert(1)> <script>x()</script>'}</Markdown>,
    );
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<script');
  });

  it('opens links in a new tab without a referrer', () => {
    const html = renderToStaticMarkup(<Markdown>{'[shop](http://shop.localhost/)'}</Markdown>);
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });
});
