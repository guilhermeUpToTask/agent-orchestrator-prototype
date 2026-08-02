import React from 'react';
import { Link, Navigate, NavLink, Route, Routes, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  BookOpen,
  Compass,
  FileCheck2,
  Gauge,
  LifeBuoy,
  Rocket,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import styles from './Docs.module.css';

/**
 * The manual, in the console.
 *
 * These are the repository's own `docs/guides/*.md` files, inlined at build
 * time — one source, two surfaces. A second hand-written copy for the browser
 * would drift from the markdown within a month, and this project treats a doc
 * that contradicts the code as a bug in the doc.
 *
 * The reason it lives in the app at all: an operator looking at a blocked plan
 * should not have to find a GitHub page to learn what the block means.
 */
const SOURCES = import.meta.glob('../../../../docs/guides/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

/** `…/docs/guides/how-it-works.md` → `how-it-works` */
function slugOf(path: string): string {
  return path.split('/').pop()!.replace(/\.md$/, '');
}

const BY_SLUG: Record<string, string> = Object.fromEntries(
  Object.entries(SOURCES).map(([path, body]) => [slugOf(path), body]),
);

type Page = { slug: string; label: string; Icon: typeof BookOpen };

/**
 * Grouped in reading order rather than alphabetically. `preview-report` is
 * listed even though invitations are Phase 9 — someone running this early
 * should be able to find how to tell us about it.
 */
const GROUPS: { title: string; pages: Page[] }[] = [
  {
    title: 'Start here',
    pages: [
      { slug: 'getting-started', label: 'Getting started', Icon: Rocket },
      { slug: 'how-it-works', label: 'How it works', Icon: Compass },
    ],
  },
  {
    title: 'Using it',
    pages: [
      { slug: 'gates', label: 'The three gates', Icon: ShieldCheck },
      { slug: 'statuses', label: 'Reading the console', Icon: Gauge },
      { slug: 'evidence', label: 'Evidence', Icon: FileCheck2 },
    ],
  },
  {
    title: 'Going further',
    pages: [
      { slug: 'tier-1', label: 'Tier 1 — real models', Icon: Sparkles },
      { slug: 'troubleshooting', label: 'Troubleshooting', Icon: LifeBuoy },
      { slug: 'preview-report', label: 'Reporting a run', Icon: BookOpen },
    ],
  },
];

const KNOWN = new Set(GROUPS.flatMap((group) => group.pages.map((page) => page.slug)));
const FIRST = GROUPS[0].pages[0].slug;

/**
 * The guides link to each other as `[text](evidence.md)`, which is correct on
 * GitHub and meaningless here. Relative `.md` targets become in-app routes;
 * anything else (http, or a path climbing out of `guides/`) stays a plain link
 * that opens externally, because the app cannot render it.
 */
function DocLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  if (href && !/^[a-z]+:/i.test(href) && href.endsWith('.md') && !href.includes('/')) {
    const slug = href.replace(/\.md$/, '');
    if (KNOWN.has(slug)) return <Link to={`/docs/${slug}`}>{children}</Link>;
  }
  const external = href?.startsWith('http');
  return (
    <a
      href={href}
      target={external ? '_blank' : undefined}
      rel={external ? 'noreferrer' : undefined}
    >
      {children}
    </a>
  );
}

/** Wide tables scroll in their own box so the page never scrolls sideways. */
function DocTable({ children }: { children?: React.ReactNode }) {
  return (
    <div className={styles.tableWrap}>
      <table>{children}</table>
    </div>
  );
}

function DocPage() {
  const { slug = FIRST } = useParams();
  const body = BY_SLUG[slug];

  React.useEffect(() => {
    document.querySelector(`.${styles.content}`)?.scrollTo(0, 0);
  }, [slug]);

  if (!body) {
    return (
      <div className={styles.content}>
        <p className={styles.missing}>
          No guide called <code>{slug}</code>. <Link to={`/docs/${FIRST}`}>Start here</Link>.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.content}>
      <article className={styles.prose}>
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: DocLink, table: DocTable }}>
          {body}
        </ReactMarkdown>
      </article>
    </div>
  );
}

export function DocsLayout() {
  return (
    <div className={styles.page}>
      <nav className={styles.nav} aria-label="Documentation">
        <div className={`label ${styles.navTitle}`}>Documentation</div>
        {GROUPS.map((group) => (
          <React.Fragment key={group.title}>
            <div className={styles.navGroup}>{group.title}</div>
            {group.pages.map(({ slug, label, Icon }) => (
              <NavLink
                key={slug}
                to={slug}
                className={({ isActive }) =>
                  `${styles.navLink} ${isActive ? styles.navActive : ''}`
                }
              >
                <Icon size={14} aria-hidden />
                {label}
              </NavLink>
            ))}
          </React.Fragment>
        ))}
        <div className={styles.navFoot}>
          These pages are this install's own <code>docs/guides/</code>, rendered here. They
          describe implemented behavior only.
        </div>
      </nav>
      <Routes>
        <Route index element={<Navigate to={FIRST} replace />} />
        <Route path=":slug" element={<DocPage />} />
        <Route path="*" element={<Navigate to={FIRST} replace />} />
      </Routes>
    </div>
  );
}
