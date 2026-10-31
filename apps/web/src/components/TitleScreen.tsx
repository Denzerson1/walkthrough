/**
 * What a visitor sees first: the still of the entry view at once, the flat's
 * title over it, and "Start tour" as soon as the 3D view is ready. Until then
 * a thin bar shows how far the flat has loaded.
 */

import { formatListingTitle, type Language } from '../lib/listing';
import type { Listing } from '../lib/manifest';

const WORDS: Record<Language, { start: string; loading: string }> = {
  en: { start: 'Start tour', loading: 'Loading the flat…' },
  de: { start: 'Rundgang starten', loading: 'Die Wohnung lädt…' },
};

interface TitleScreenProps {
  name: string;
  listing?: Listing | null;
  stillUrl?: string | null;
  language: Language;
  ready: boolean;
  /** How much of the flat has arrived, 0..1 (0: not known yet). */
  progress: number;
  /** Fading out: the tour has started. */
  leaving: boolean;
  onStart: () => void;
  /** Faded out: it can go. */
  onGone: () => void;
}

export function TitleScreen({
  name,
  listing,
  stillUrl,
  language,
  ready,
  progress,
  leaving,
  onStart,
  onGone,
}: TitleScreenProps) {
  const words = WORDS[language];
  const title = listing ? formatListingTitle(listing, language) : '';
  return (
    <section
      className={[
        'absolute inset-0 z-20 flex flex-col justify-end bg-[var(--paper-warm)] transition-opacity duration-500',
        leaving ? 'pointer-events-none opacity-0' : 'opacity-100',
      ].join(' ')}
      style={{
        backgroundImage: stillUrl ? `url(${stillUrl})` : undefined,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }}
      aria-hidden={leaving}
      onTransitionEnd={(e) => leaving && e.target === e.currentTarget && onGone()}
      data-testid="title-screen"
    >
      <div
        className="px-5 pt-24 text-[var(--paper)]"
        style={{
          paddingBottom: 'calc(2rem + var(--safe-bottom))',
          background: 'linear-gradient(to top, rgba(20,16,12,0.72), rgba(20,16,12,0.35) 60%, transparent)',
        }}
      >
        <div className="mx-auto max-w-[36rem]">
          <h1 className="text-[26px] font-semibold leading-tight tracking-tight">{name}</h1>
          {title && (
            <p className="measure mt-1.5 text-[14px] opacity-90" data-testid="listing-title">
              {title}
            </p>
          )}
          <button
            type="button"
            onClick={onStart}
            disabled={!ready}
            className="tap mt-5 min-h-12 w-full rounded-[10px] bg-[var(--paper)] px-5 text-[15px] font-semibold text-[var(--ink)] transition-opacity disabled:opacity-80 sm:w-auto"
            data-testid="start-tour"
          >
            {ready ? words.start : words.loading}
          </button>
          {!ready && (
            <div className="mt-3 h-[3px] w-full overflow-hidden rounded-full bg-white/25 sm:w-64" aria-hidden="true">
              <div
                className="h-full rounded-full bg-white/90 transition-[width] duration-300"
                style={{ width: `${Math.max(progress, 0.06) * 100}%` }}
              />
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
