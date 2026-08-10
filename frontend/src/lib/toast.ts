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
 * api.ts throws `Error("POST /path → 409: {"error":{...}}")`. We surface the
 * server's message when we recognise the shape.
 *
 * When we do NOT recognise it, we report the status and drop the body. That
 * asymmetry is deliberate (Phase 10A). This used to fall back to the raw
 * message, and a request body can contain a credential: FastAPI's default 422
 * echoed the submitted `api_key` inside `detail[].input`, and because that
 * `detail` is an ARRAY the string check below missed it and the whole payload —
 * key included — was rendered into a toast. The backend no longer emits that
 * shape, but a helper whose failure mode is "print whatever came back" is one
 * bad response away from doing it again, so an unrecognised body is never shown.
 */
export function errorDetail(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const jsonStart = message.indexOf('{');
  if (jsonStart === -1) return message; // no body — the bare "METHOD /path → 500"

  const prefix = message.slice(0, jsonStart).trim().replace(/[:→-]\s*$/, '');
  try {
    const parsed = JSON.parse(message.slice(jsonStart));
    // Control-plane envelope: { error: { code, message, request_id } }.
    if (parsed?.error && typeof parsed.error.message === 'string') {
      const rid = parsed.error.request_id ? ` (request ${parsed.error.request_id})` : '';
      return `${parsed.error.message}${rid}`;
    }
    // A plain-string `detail` is a message, not an echo of the request.
    if (parsed && typeof parsed.detail === 'string') return parsed.detail;
  } catch {
    // not JSON
  }
  // Unrecognised body: say what failed, never what was sent.
  return prefix || message.slice(0, jsonStart) || 'The server returned an unreadable error.';
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
