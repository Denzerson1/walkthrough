/** Bottom-sheet panels: floors, furniture and the assistant. */

import { useEffect, useMemo, useState } from 'react';
import { floorColor } from '../lib/floorColors';
import type { CatalogItem } from '../lib/store';
import { furnitureThumbnail } from '../lib/thumbnails';

interface SheetProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

export function Sheet({ title, onClose, children }: SheetProps) {
  return (
    <section
      className="panel rule flex max-h-[58vh] flex-col border-t"
      role="dialog"
      aria-label={title}
      data-testid={`sheet-${title.toLowerCase()}`}
    >
      <header className="rule flex items-center justify-between border-b px-4 py-2.5">
        <h2 className="text-[13px] font-semibold tracking-tight">{title}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label={`Close ${title}`}
          className="-mr-1 px-2 py-1 text-[18px] leading-none opacity-70 hover:opacity-100"
        >
          ×
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------

interface FloorPanelProps {
  floors: CatalogItem[];
  appliedFloorId?: string;
  roomName: string;
  onApply: (floorId: string, allRooms: boolean) => void;
  onClear: () => void;
}

export function FloorPanel({
  floors,
  appliedFloorId,
  roomName,
  onApply,
  onClear,
}: FloorPanelProps) {
  const [allRooms, setAllRooms] = useState(false);
  const categories = [...new Set(floors.map((f) => f.category ?? 'other'))];
  const [category, setCategory] = useState<string>('all');
  const shown = category === 'all' ? floors : floors.filter((f) => f.category === category);

  if (!floors.length) {
    return (
      <p className="px-4 py-6 text-[13px] opacity-70">
        No floors are seeded yet. Run <code className="measure">pnpm seed:floors</code> to add
        the CC0 material set.
      </p>
    );
  }

  return (
    <div>
      <div className="scroll-x rule flex gap-1 border-b px-3 py-2">
        {['all', ...categories].map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => setCategory(c)}
            aria-pressed={category === c}
            className={[
              'shrink-0 px-2.5 py-1 text-[12px] capitalize',
              category === c ? 'bg-[var(--accent)]' : 'opacity-65 hover:opacity-100',
            ].join(' ')}
          >
            {c}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-2 p-3 sm:grid-cols-5 lg:grid-cols-8">
        {shown.map((floor) => {
          const applied = floor.id === appliedFloorId;
          return (
            <button
              key={floor.id}
              type="button"
              onClick={() => onApply(floor.id, allRooms)}
              aria-pressed={applied}
              data-floor-id={floor.id}
              className="group text-left"
            >
              <span
                className="block aspect-square w-full rounded-sm border"
                style={{
                  background: floor.thumbnail
                    ? `center/cover url(${floor.thumbnail})`
                    : swatchFor(floor),
                  borderColor: applied ? 'var(--accent)' : 'var(--line)',
                  borderWidth: applied ? 2 : 1,
                }}
              />
              <span className="mt-1 block text-[11px] leading-tight opacity-85">
                {floor.name}
              </span>
              {floor.tileSizeM && (
                <span className="measure block text-[10px] opacity-50">
                  {(floor.tileSizeM[0] * 100).toFixed(0)}×
                  {(floor.tileSizeM[1] * 100).toFixed(0)} cm
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="rule flex items-center justify-between gap-3 border-t px-4 py-3">
        <label className="flex items-center gap-2 text-[12px]">
          <input
            type="checkbox"
            checked={allRooms}
            onChange={(e) => setAllRooms(e.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--accent)]"
          />
          Apply to every room
        </label>
        {appliedFloorId && (
          <button
            type="button"
            onClick={onClear}
            className="text-[12px] underline underline-offset-2 opacity-75 hover:opacity-100"
          >
            Restore {roomName} floor
          </button>
        )}
      </div>
    </div>
  );
}

function swatchFor(floor: CatalogItem): string {
  return floorColor(floor);
}

// ---------------------------------------------------------------------------

interface FurniturePanelProps {
  furniture: CatalogItem[];
  styles: CatalogItem[];
  onAdd: (itemId: string) => void;
  onApplyStyle: (styleId: string) => void;
  onAutoLayout: (styleId: string) => void;
}

/**
 * One furniture tile: a render of the real mesh once it arrives, over the
 * item's colour. The colour is the placeholder rather than a grey box, so a
 * tile looks deliberate while its model loads and if the model never comes.
 */
function FurnitureTile({ item }: { item: CatalogItem }) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!item.glb) return;
    let cancelled = false;
    furnitureThumbnail(item.id, item.glb, item.colors?.[0])
      .then((url) => !cancelled && setSrc(url))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [item.id, item.glb, item.colors]);

  return (
    <span
      className="rule relative flex aspect-square w-full items-end justify-start overflow-hidden rounded-md border p-1.5"
      style={{ background: src ? 'var(--paper-warm)' : (item.colors?.[0] ?? '#b3aa9c') }}
    >
      {src && (
        <img
          src={src}
          alt=""
          aria-hidden="true"
          className="absolute inset-0 h-full w-full object-contain"
        />
      )}
      <span
        className={`measure relative text-[9px] ${src ? 'text-[var(--ink)] opacity-55' : 'text-[var(--paper)] opacity-85'}`}
      >
        {item.dimensionsM
          ? `${item.dimensionsM[0].toFixed(1)}×${item.dimensionsM[2].toFixed(1)}`
          : ''}
      </span>
    </span>
  );
}

export function FurniturePanel({
  furniture,
  styles,
  onAdd,
  onApplyStyle,
  onAutoLayout,
}: FurniturePanelProps) {
  // Stocked styles first, so the obvious first click is not one that fails.
  const ordered = [...styles].sort(
    (a, b) => Number(b.complete ?? false) - Number(a.complete ?? false),
  );
  const [styleId, setStyleId] = useState<string>(
    ordered.find((s) => s.id === 'modern')?.id ?? ordered[0]?.id ?? '',
  );
  const selected = ordered.find((s) => s.id === styleId);
  const unstocked = selected ? selected.complete === false : false;

  // Each piece belongs to exactly one theme, and the grid shows the chosen
  // theme's pieces only. Listing all 47 together put a carved gothic cabinet
  // next to a steel shelf and made the themes meaningless.
  const shown = useMemo(
    () => furniture.filter((item) => !styleId || item.styles?.includes(styleId)),
    [furniture, styleId],
  );

  if (!furniture.length && !styles.length) {
    return (
      <p className="px-4 py-6 text-[13px] opacity-70">
        No furniture is seeded yet. Run <code className="measure">pnpm seed:assets</code>.
      </p>
    );
  }

  return (
    <div>
      {styles.length > 0 && (
        <div className="rule border-b px-3 py-3">
          <div className="scroll-x flex gap-1.5">
            {ordered.map((style) => (
              <button
                key={style.id}
                type="button"
                onClick={() => setStyleId(style.id)}
                aria-pressed={styleId === style.id}
                className={[
                  'btn shrink-0 px-3 py-1.5 text-[12px]',
                  style.complete === false ? 'italic' : '',
                ].join(' ')}
              >
                {style.name}
                {style.complete === false && (
                  <span className="ml-1.5 opacity-55">· no items yet</span>
                )}
              </button>
            ))}
          </div>
          <div className="mt-2.5 flex gap-2">
            <button
              type="button"
              onClick={() => onApplyStyle(styleId)}
              disabled={!styleId}
              className="btn-primary flex-1 px-3 py-2 text-[12px]"
            >
              Apply style
            </button>
            <button
              type="button"
              onClick={() => onAutoLayout(styleId)}
              disabled={!styleId || unstocked}
              className="btn flex-1 px-3 py-2 text-[12px]"
            >
              Furnish automatically
            </button>
          </div>
          {unstocked && (
            <p className="mt-2 text-[12px] opacity-60">
              {selected?.name} has a floor and a palette but no furniture yet. Its
              floor still applies.
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2 p-3 sm:grid-cols-5 lg:grid-cols-8">
        {shown.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onAdd(item.id)}
            data-furniture-id={item.id}
            className="text-left"
          >
            <FurnitureTile item={item} />
            <span className="mt-1 block text-[11px] leading-tight opacity-85">{item.name}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
