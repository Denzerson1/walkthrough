/**
 * Compare and reset controls for a modified view.
 *
 * This used to carry a "Virtually staged" chip as well, which the brief
 * called non-negotiable. The owner has removed it — see CLAUDE.md. Holding
 * Compare still shows the real capture, and Reset still discards every
 * change, so the honest view is one press away even without the label.
 */

interface Props {
  staged: boolean;
  showingOriginal: boolean;
  onHoldStart: () => void;
  onHoldEnd: () => void;
  onReset: () => void;
}

export function StagedBadge({
  staged,
  showingOriginal,
  onHoldStart,
  onHoldEnd,
  onReset,
}: Props) {
  if (!staged) return null;

  return (
    <div
      className="flex items-center gap-1"
      data-testid="staged-badge"
    >
      <button
        type="button"
        onPointerDown={onHoldStart}
        onPointerUp={onHoldEnd}
        onPointerLeave={onHoldEnd}
        onPointerCancel={onHoldEnd}
        aria-pressed={showingOriginal}
        className="panel whitespace-nowrap rounded-[var(--radius)] px-3 py-1.5 text-[12px] font-medium hover:bg-[var(--paper)]"
        data-testid="hold-original"
      >
        <span className="hidden sm:inline">
          {showingOriginal ? 'Showing original' : 'Hold to compare'}
        </span>
        <span className="sm:hidden">{showingOriginal ? 'Original' : 'Compare'}</span>
      </button>

      <button
        type="button"
        onClick={onReset}
        className="panel whitespace-nowrap rounded-[var(--radius)] px-3 py-1.5 text-[12px] font-medium hover:bg-[var(--paper)]"
        data-testid="reset-all"
      >
        Reset
      </button>
    </div>
  );
}
