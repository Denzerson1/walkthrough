/** Bottom-sheet panels: floors, furniture and the assistant. */

import { useState } from 'react';
import type { CatalogItem } from '../lib/store';

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
              category === c ? 'bg-[var(--blueprint)]' : 'opacity-65 hover:opacity-100',
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
                  borderColor: applied ? 'var(--blueprint)' : 'var(--line)',
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
            className="h-3.5 w-3.5 accent-[var(--blueprint)]"
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
  const palette: Record<string, string> = {
    wood: 'linear-gradient(100deg,#9a7247,#b58a57)',
    tile: 'linear-gradient(100deg,#b9b2a6,#cfc9bd)',
    stone: 'linear-gradient(100deg,#8e8e8a,#a5a5a0)',
    painted: 'linear-gradient(100deg,#d8d3c8,#e6e2d9)',
    stencilled: 'linear-gradient(100deg,#c9c2b4,#ded8ca)',
    vintage: 'linear-gradient(100deg,#a08a6d,#b9a382)',
  };
  return palette[floor.category ?? ''] ?? '#a89a86';
}

// ---------------------------------------------------------------------------

interface FurniturePanelProps {
  furniture: CatalogItem[];
  styles: CatalogItem[];
  onAdd: (itemId: string) => void;
  onApplyStyle: (styleId: string) => void;
  onAutoLayout: (styleId: string) => void;
}

export function FurniturePanel({
  furniture,
  styles,
  onAdd,
  onApplyStyle,
  onAutoLayout,
}: FurniturePanelProps) {
  const [styleId, setStyleId] = useState<string>(styles[0]?.id ?? '');

  if (!furniture.length && !styles.length) {
    return (
      <p className="px-4 py-6 text-[13px] opacity-70">
        No furniture is seeded yet. Run <code className="measure">pnpm seed:furniture</code>.
      </p>
    );
  }

  return (
    <div>
      {styles.length > 0 && (
        <div className="rule border-b px-3 py-3">
          <div className="scroll-x flex gap-1.5">
            {styles.map((style) => (
              <button
                key={style.id}
                type="button"
                onClick={() => setStyleId(style.id)}
                aria-pressed={styleId === style.id}
                className={[
                  'shrink-0 px-3 py-1.5 text-[12px]',
                  styleId === style.id
                    ? 'bg-[var(--blueprint)]'
                    : 'rule border opacity-70 hover:opacity-100',
                ].join(' ')}
              >
                {style.name}
              </button>
            ))}
          </div>
          <div className="mt-2.5 flex gap-2">
            <button
              type="button"
              onClick={() => onApplyStyle(styleId)}
              disabled={!styleId}
              className="flex-1 bg-[var(--paper)] px-3 py-2 text-[12px] font-semibold text-[#14171C] disabled:opacity-40"
            >
              Apply style
            </button>
            <button
              type="button"
              onClick={() => onAutoLayout(styleId)}
              disabled={!styleId}
              className="rule flex-1 border px-3 py-2 text-[12px] font-medium disabled:opacity-40"
            >
              Furnish automatically
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-3 gap-2 p-3 sm:grid-cols-5 lg:grid-cols-8">
        {furniture.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onAdd(item.id)}
            data-furniture-id={item.id}
            className="text-left"
          >
            <span
              className="rule flex aspect-square w-full items-end justify-start rounded-sm border p-1.5"
              style={{ background: item.colors?.[0] ?? '#3a3f49' }}
            >
              <span className="measure text-[9px] text-[#14171C] opacity-80">
                {item.dimensionsM
                  ? `${item.dimensionsM[0].toFixed(1)}×${item.dimensionsM[2].toFixed(1)}`
                  : ''}
              </span>
            </span>
            <span className="mt-1 block text-[11px] leading-tight opacity-85">{item.name}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
