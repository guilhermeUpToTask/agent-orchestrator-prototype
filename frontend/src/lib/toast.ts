/**
 * src/lib/toast.ts
 *
 * Lightweight toast notifications. Flow errors (failed approvals, failed
 * planner runs, backend communication errors) surface here as dismissable
 * toasts instead of being buried as system lines in the chat transcript,
 * where the operator was missing them.
 *
 * A tiny standalone zustand store so it can be driven from anywhere —
 * including non-component code (React Query mutation handlers) via the
 * `toast` helper, which reads the store imperatively.
 */

import { create } from 'zustand';
import { nanoid } from 'nanoid';

export type ToastKind = 'error' | 'success' | 'info';

export interface Toast {
  id: string;
  kind: ToastKind;
  title: string;
  detail?: string;
}

// Non-errors auto-dismiss; errors stay until the operator dismisses them.
const AUTO_DISMISS_MS = 6000;

interface ToastState {
  toasts: Toast[];
  push: (t: Omit<Toast, 'id'>) => string;
  dismiss: (id: string) => void;
}

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (t) => {
    const id = nanoid();
    set((s) => ({ toasts: [...s.toasts, { id, ...t }] }));
    if (t.kind !== 'error') {
      setTimeout(
        () => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
        AUTO_DISMISS_MS,
      );
    }
    return id;
  },
  dismiss: (id) =>
    set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
}));

/**
 * Pull the human-readable detail out of an api.ts error.
 *
 * api.ts throws `Error("POST /path → 409: {"detail":"..."}")`. We surface the
 * server's `detail` when present, falling back to the raw message.
 */
export function errorDetail(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const jsonStart = message.indexOf('{');
  if (jsonStart !== -1) {
    try {
      const parsed = JSON.parse(message.slice(jsonStart));
      if (parsed && typeof parsed.detail === 'string') return parsed.detail;
    } catch {
      // not JSON — fall through to the raw message
    }
  }
  return message;
}

/** Imperative helpers usable from mutation handlers and plain functions. */
export const toast = {
  error: (title: string, detail?: string) =>
    useToastStore.getState().push({ kind: 'error', title, detail }),
  success: (title: string, detail?: string) =>
    useToastStore.getState().push({ kind: 'success', title, detail }),
  info: (title: string, detail?: string) =>
    useToastStore.getState().push({ kind: 'info', title, detail }),
};
