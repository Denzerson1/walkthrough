/** The one-line note a change leaves behind, with Undo while it can be taken back. */

import { useEffect } from 'react';
import type { Toast as ToastState } from '../lib/store';

/** How long a toast stays, ms: long enough to reach Undo with a thumb. */
const SHOWN_MS = 5000;

interface ToastProps {
  toast: ToastState | null;
  onUndo: () => void;
  onDismiss: () => void;
}

export function Toast({ toast, onUndo, onDismiss }: ToastProps) {
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(onDismiss, SHOWN_MS);
    return () => window.clearTimeout(timer);
  }, [toast, onDismiss]);

  if (!toast) return null;
  return (
    <div className="flex justify-center" role="status" aria-live="polite">
      <div
        key={toast.id}
        className="panel flex min-h-11 max-w-full items-center gap-1 rounded-full py-1 pl-4 pr-1.5"
        style={{ animation: 'fade-up 200ms ease-out' }}
        data-testid="toast"
      >
        <span className="truncate text-[13px]">{toast.message}</span>
        {toast.before && (
          <button
            type="button"
            onClick={onUndo}
            className="tap ml-1 min-h-9 shrink-0 rounded-full px-3.5 text-[13px] font-semibold text-[var(--accent)] transition-colors hover:bg-[var(--accent-soft)]"
            data-testid="toast-undo"
          >
            Undo
          </button>
        )}
      </div>
    </div>
  );
}
