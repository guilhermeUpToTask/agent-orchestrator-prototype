import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { DocsLayout } from './DocsLayout';

/**
 * The console's manual is the repository's own `docs/guides/*.md`, pulled in by
 * a relative `import.meta.glob`. That path is the fragile part: move the view,
 * move the guides, or change the build root, and the glob quietly matches
 * nothing — the nav still renders, every page 404s, and nothing fails. These
 * tests exist so that failure is loud.
 */
function render(path: string): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/docs/*" element={<DocsLayout />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('in-console documentation', () => {
  it('renders a guide’s real body, not an empty shell', () => {
    const html = render('/docs/how-it-works');

    // Headings only: prose assertions are brittle, because markdown source
    // wraps and the rendered text keeps the newline mid-sentence.
    expect(html).toContain('Where the code goes');
    // Rendered as markdown rather than dumped as text.
    expect(html).toContain('<h2');
  });

  it('renders every guide the nav offers', () => {
    // A nav entry whose file is missing is worse than no entry: it advertises
    // a page and then tells the reader it does not exist.
    for (const slug of [
      'getting-started',
      'how-it-works',
      'gates',
      'statuses',
      'evidence',
      'tier-1',
      'troubleshooting',
    ]) {
      expect(render(`/docs/${slug}`), slug).not.toContain('No guide called');
    }
  });

  it('rewrites cross-guide markdown links to in-app routes', () => {
    // `[statuses.md](statuses.md)` is correct on GitHub and a dead link here.
    const html = render('/docs/how-it-works');

    expect(html).toContain('href="/docs/statuses"');
    expect(html).not.toContain('href="statuses.md"');
  });

  it('opens external links in a new tab', () => {
    const html = render('/docs/evidence');
    if (html.includes('href="http')) {
      expect(html).toContain('target="_blank"');
    }
  });

  it('says so plainly when a slug does not exist', () => {
    expect(render('/docs/not-a-guide')).toContain('No guide called');
  });

  it('renders GFM tables inside their own scroll container', () => {
    // Wide tables must scroll in a box; the page body never scrolls sideways.
    const html = render('/docs/statuses');

    expect(html).toContain('<table>');
    expect(html).toMatch(/class="[^"]*tableWrap[^"]*"/);
  });
});
