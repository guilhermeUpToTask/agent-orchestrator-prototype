import { useEffect, useState } from 'react';

/** Ticking clock for live relative timestamps. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** "2s ago" / "4m ago" / "3h ago" — pair with `absTime` in a title attr. */
export function relTime(at: number | string | Date | null | undefined, now: number): string {
  if (at == null) return '—';
  const t = typeof at === 'number' ? at : new Date(at).getTime();
  if (Number.isNaN(t)) return '—';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Full absolute timestamp for title attributes. */
export function absTime(at: number | string | Date | null | undefined): string {
  if (at == null) return '';
  const d = typeof at === 'number' ? new Date(at) : new Date(at);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-US', {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

/** "4m 12s" elapsed since a start time. */
export function elapsed(since: number | string | Date, now: number): string {
  const t = typeof since === 'number' ? since : new Date(since).getTime();
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
