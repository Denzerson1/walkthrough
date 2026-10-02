/**
 * "Virtually staged" badge.
 *
 * The brief makes this non-negotiable: any modified view says so visibly, and
 * the original is one tap away. Holding the button compares against the real
 * capture; tapping Reset discards the changes.
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
      className="flex items-center gap-px overflow-hidden rounded-sm"
      data-testid="staged-badge"
    >
      <span
        className="flex items-center gap-1.5 whitespace-nowrap px-2.5 py-2 text-[12px] font-semibold tracking-tight text-[#14171C]"
        style={{ background: 'var(--staged)' }}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M6 1 L11 10.5 L1 10.5 Z" fill="none" stroke="#14171C" strokeWidth="1.4" />
          <path d="M6 4.6 v2.6" stroke="#14171C" strokeWidth="1.4" strokeLinecap="round" />
          <circle cx="6" cy="8.9" r="0.7" fill="#14171C" />
        </svg>
        {/* The full phrase is the point of this badge, but on a phone it
            would wrap onto three lines and eat the top of the screen. */}
        <span className="hidden sm:inline">Virtually staged</span>
        <span className="sm:hidden">Staged</span>
      </span>

      <button
        type="button"
        onPointerDown={onHoldStart}
        onPointerUp={onHoldEnd}
        onPointerLeave={onHoldEnd}
        onPointerCancel={onHoldEnd}
        aria-pressed={showingOriginal}
        className="panel whitespace-nowrap px-2.5 py-2 text-[12px] font-medium hover:bg-[rgba(42,47,56,0.95)]"
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
        className="panel whitespace-nowrap px-2.5 py-2 text-[12px] font-medium hover:bg-[rgba(42,47,56,0.95)]"
        data-testid="reset-all"
      >
        Reset
      </button>
    </div>
  );
}
