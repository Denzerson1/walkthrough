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
    <div className="flex justify-center px-3 pb-2" role="status" aria-live="polite">
      <div
        key={toast.id}
        className="panel flex min-h-11 items-center gap-1 rounded-[9px] py-1 pl-4 pr-1"
        style={{ animation: 'fade-up 200ms ease-out' }}
        data-testid="toast"
      >
        <span className="text-[13px]">{toast.message}</span>
        {toast.before && (
          <button
            type="button"
            onClick={onUndo}
            className="tap ml-2 min-h-11 rounded-[var(--radius-sm)] px-3 text-[13px] font-semibold text-[var(--accent)] underline-offset-2 hover:underline"
            data-testid="toast-undo"
          >
            Undo
          </button>
        )}
      </div>
    </div>
  );
}
