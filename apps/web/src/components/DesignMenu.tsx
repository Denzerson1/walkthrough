/**
 * The design menu: a style for the flat, floors room by room, every piece
 * of furniture, and the finishes of the kitchen and the wardrobe. A bottom
 * sheet on a phone, collapsed to one line until opened; a panel at the right
 * on a big screen. The same tabs wherever you stand, pictures with a word
 * under each, choices opening inside their tab, and one button back to the
 * original. It reads and changes the one store the 3D taps and the assistant
 * use, so all three always agree.
 */

import { useEffect, useMemo, useState } from 'react';
import { apiUrl } from '../lib/api';
import { type Finish, type Fitting, FITTINGS } from '../lib/finishes';
import { floorColor } from '../lib/floorColors';
import type { Room, Slot } from '../lib/manifest';
import { type CatalogItem, useStore } from '../lib/store';
import { alternatives, baseFor, kitReady } from '../lib/styles';
import { isStaged } from '../lib/viewerState';

export type DesignTab = 'style' | 'floor' | 'furniture' | Fitting;

/** How a slot's category reads under its tile. */
const CATEGORY_NAMES: Record<string, string> = {
  sofa: 'Sofa',
  armchair: 'Armchair',
  coffee_table: 'Coffee table',
  side_table: 'Side table',
  rug: 'Rug',
  floor_lamp: 'Floor lamp',
  table_lamp: 'Table lamp',
  pendant_lamp: 'Pendant',
  bed: 'Bed',
  nightstand: 'Nightstand',
  dresser: 'Dresser',
  dining_table: 'Table',
  dining_chair: 'Chair',
  desk: 'Desk',
  desk_chair: 'Desk chair',
  shelf: 'Shelf',
};

const categoryName = (category: string) => CATEGORY_NAMES[category] ?? category.replace(/_/g, ' ');

/** Where a floor is laid: the room you stand in, or every room. */
type FloorScope = 'room' | 'all';

/** A slot being chosen for, in its room. */
interface Picked {
  roomId: string;
  slotId: string;
}

interface DesignMenuProps {
  /** The room you stand in. */
  room: Room;
  /** The fittings this flat has finishes for (finishes.fittingsIn). */
  fittings: Fitting[];
  /** A phone: the bottom sheet; otherwise the side panel. */
  compact: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The tab shown, or null while the menu is closed: the view follows it. */
  onTabShown?: (tab: DesignTab | null) => void;
}

export function DesignMenu({ room, fittings, compact, open, onOpenChange, onTabShown }: DesignMenuProps) {
  const {
    manifest, viewer, catalog, selectedUid,
    applyStyle, removeStyle, swapPiece, clearSlot, setFloor, setFinish, reset, selectItem, goToRoom,
  } = useStore();
  const [tab, setTab] = useState<DesignTab>('style');
  const [picked, setPicked] = useState<Picked | null>(null);
  const [scope, setScope] = useState<FloorScope>('room');
  useEffect(() => onTabShown?.(open ? tab : null), [open, tab, onTabShown]);

  const rooms = useMemo(() => manifest?.rooms ?? [], [manifest]);
  const furnished = useMemo(() => rooms.filter((r) => r.slots?.length), [rooms]);
  const kits = useMemo(
    () =>
      catalog.styles.filter((k) =>
        furnished.some((r) => kitReady(k, r.type, r.slots ?? [], catalog.furniture)),
      ),
    [catalog.styles, catalog.furniture, furnished],
  );
  const styleIds = [...new Set(Object.values(viewer.styles))];
  const style = styleIds.length === 1 ? catalog.styles.find((k) => k.id === styleIds[0]) : undefined;
  const changed = isStaged(viewer);

  // Which tabs hold a change, for the dot on each.
  const touched: Record<DesignTab, boolean> = {
    style: styleIds.length > 0,
    floor: Object.keys(viewer.floors).length > 0,
    furniture: viewer.items.some((i) => i.slotId),
    kitchen: Boolean(viewer.finishes.kitchen),
    wardrobe: Boolean(viewer.finishes.wardrobe),
  };
  const tabs: Array<[DesignTab, string]> = [
    ['style', 'Style'],
    ['floor', 'Floor'],
    ['furniture', 'Furniture'],
    ...FITTINGS.filter((f) => fittings.includes(f.id)).map((f) => [f.id, f.name] as [DesignTab, string]),
  ];

  // A piece of furniture tapped in 3D opens its choices here (set while
  // rendering, as React advises for state that follows a prop).
  const tapped = viewer.items.find((i) => i.uid === selectedUid);
  const tappedSlot = tapped?.slotId ? `${tapped.roomId}/${tapped.slotId}` : null;
  const [seenTap, setSeenTap] = useState(tappedSlot);
  if (tappedSlot !== seenTap) {
    setSeenTap(tappedSlot);
    if (tapped?.slotId) {
      setTab('furniture');
      setPicked({ roomId: tapped.roomId, slotId: tapped.slotId });
    }
  }

  const pieceIn = (roomId: string, slot: Slot) => {
    const placed = viewer.items.find((i) => i.roomId === roomId && i.slotId === slot.id);
    return placed ? catalog.furniture.find((f) => f.id === placed.itemId) : undefined;
  };
  const pickedRoom = rooms.find((r) => r.id === picked?.roomId);
  const pickedSlot = pickedRoom?.slots?.find((s) => s.id === picked?.slotId);
  const closeChoices = () => {
    setPicked(null);
    selectItem(null);
  };

  const header = (
    <button
      type="button"
      onClick={() => onOpenChange(!open)}
      aria-expanded={open}
      aria-controls="design-menu-body"
      className="tap flex min-h-14 w-full items-center gap-3 px-3 py-2 text-left"
      data-testid="design-menu-toggle"
    >
      <span
        aria-hidden="true"
        className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-[10px] bg-[var(--accent-soft)] text-[var(--accent)]"
      >
        {style ? <Stripes colours={style.swatches} /> : <PaletteIcon />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-semibold leading-tight tracking-tight">Design</span>
        <span className="mt-0.5 flex items-center gap-1.5 truncate text-[12px] leading-tight opacity-70">
          {changed && <Dot />}
          {style ? style.name : changed ? 'Your changes' : 'Original'}
        </span>
      </span>
      <span
        aria-hidden="true"
        className={[
          'grid h-9 w-9 shrink-0 place-items-center rounded-full transition-colors',
          open ? 'bg-[var(--paper-warm)] text-[var(--ink)]' : 'bg-[var(--accent)] text-[var(--paper)]',
        ].join(' ')}
      >
        <Chevron direction={compact ? (open ? 'down' : 'up') : open ? 'right' : 'left'} />
      </span>
      <span className="sr-only">{open ? 'Close' : 'Open'}</span>
    </button>
  );

  const styleTab = (
    <Grid>
      <Choice label="None" selected={!styleIds.length} onClick={removeStyle} testId="style-none">
        <OriginalIcon />
      </Choice>
      {kits.map((k) => (
        <Choice
          key={k.id}
          label={k.name}
          selected={style?.id === k.id}
          onClick={() => applyStyle('all', k.id)}
          data={{ 'data-style-id': k.id }}
        >
          <Stripes colours={k.swatches} />
        </Choice>
      ))}
    </Grid>
  );

  // The floor every room has, when they all have the same; for the whole flat.
  const firstFloor = viewer.floors[rooms[0]?.id ?? ''];
  const shared = rooms.every((r) => viewer.floors[r.id] === firstFloor) ? (firstFloor ?? null) : undefined;
  const floorNow = scope === 'all' ? shared : (viewer.floors[room.id] ?? null);
  const floorTargets = scope === 'all' ? ['all'] : [room.id];
  const floorTab = (
    <>
      {rooms.length > 1 && (
        <Segmented
          label="Where"
          options={[
            ['all', 'Whole flat'],
            ...rooms.map((r): [string, string] => [r.id, r.name]),
          ]}
          value={scope === 'all' ? 'all' : room.id}
          onChange={(id) => {
            if (id === 'all') return setScope('all');
            setScope('room');
            // Walk there, to see the floor laid.
            if (id !== room.id) goToRoom(id);
          }}
          testId="floor-scope"
        />
      )}
      <Grid>
        <Choice
          label="Original"
          selected={floorNow === null}
          onClick={() => setFloor(floorTargets, null)}
          testId="floor-original"
        >
          <OriginalIcon />
        </Choice>
        {catalog.floors.map((floor) => (
          <Choice
            key={floor.id}
            label={floor.name}
            selected={floor.id === floorNow}
            onClick={() => setFloor(floorTargets, floor.id)}
            data={{ 'data-floor-id': floor.id }}
          >
            <span
              className="block h-full w-full"
              // Its colour shows until the picture is in, or if it never comes.
              style={{
                background: [floor.thumbnail && `url(${apiUrl(floor.thumbnail)}) center / cover`, floorColor(floor)]
                  .filter(Boolean)
                  .join(', '),
              }}
            />
          </Choice>
        ))}
      </Grid>
    </>
  );

  const furnitureTab =
    pickedRoom && pickedSlot ? (
      <Alternatives
        slot={pickedSlot}
        room={pickedRoom}
        current={pieceIn(pickedRoom.id, pickedSlot)}
        onBack={closeChoices}
        onChoose={(itemId) => swapPiece(pickedRoom.id, pickedSlot.id, itemId)}
        onClear={() => clearSlot(pickedRoom.id, pickedSlot.id)}
      />
    ) : (
      <div className="pb-1">
        {furnished.map((r) => (
          <section key={r.id}>
            <h3 className="flex items-center gap-2 px-3 pt-3 text-[11px] font-semibold uppercase tracking-[0.06em] opacity-60">
              {r.name}
              {r.id === room.id && <span className="font-medium normal-case tracking-normal">· you are here</span>}
            </h3>
            <Grid>
              {(r.slots ?? []).map((slot) => {
                const piece = pieceIn(r.id, slot);
                return (
                  <Choice
                    key={slot.id}
                    label={categoryName(slot.category)}
                    changed={Boolean(piece)}
                    onClick={() => {
                      // Walk there, to see what you choose.
                      if (r.id !== room.id) goToRoom(r.id);
                      setPicked({ roomId: r.id, slotId: slot.id });
                    }}
                    data={{ 'data-slot-id': slot.id }}
                  >
                    {piece ? <Thumbnail item={piece} /> : <PlusIcon />}
                  </Choice>
                );
              })}
            </Grid>
          </section>
        ))}
      </div>
    );

  const fittingTab = (fitting: Fitting) => (
    <Grid>
      <Choice
        label="Original"
        selected={!viewer.finishes[fitting]}
        onClick={() => setFinish(fitting, null)}
        testId={`${fitting}-original`}
      >
        <OriginalIcon />
      </Choice>
      {catalog.finishes
        .filter((f) => f.fitting === fitting)
        .map((finish) => (
          <Choice
            key={finish.id}
            label={finish.name}
            selected={viewer.finishes[fitting] === finish.id}
            onClick={() => setFinish(fitting, finish.id)}
            data={{ 'data-finish-id': finish.id }}
          >
            <FinishSwatch finish={finish} />
          </Choice>
        ))}
    </Grid>
  );

  const body = (
    <>
      {/* Fixed in height: the list below takes what room the sheet has. */}
      <div className="scroll-x shrink-0 px-3 pb-2">
        <div
          role="tablist"
          aria-label="Design"
          className="grid min-w-full auto-cols-[minmax(max-content,1fr)] grid-flow-col gap-0.5 rounded-full bg-[var(--paper-warm)] p-1"
        >
          {tabs.map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => {
                setTab(id);
                closeChoices();
              }}
              className={[
                'tap relative flex min-h-9 items-center justify-center gap-1 rounded-full px-3 text-[12.5px] transition-[background-color,box-shadow,color]',
                tab === id
                  ? 'bg-[var(--paper)] font-semibold text-[var(--accent)] shadow-[var(--shadow-raised)]'
                  : 'font-medium text-[var(--ink-soft)] hover:text-[var(--ink)]',
              ].join(' ')}
              data-testid={`design-tab-${id}`}
            >
              {label}
              {touched[id] && <Dot label="Changed" />}
            </button>
          ))}
        </div>
      </div>
      <div role="tabpanel" className="rule min-h-0 flex-1 overflow-y-auto overscroll-contain border-t">
        {tab === 'style' && styleTab}
        {tab === 'floor' && floorTab}
        {tab === 'furniture' && furnitureTab}
        {(tab === 'kitchen' || tab === 'wardrobe') && fittingTab(tab)}
        {(tab === 'style' || tab === 'furniture') && <Credit />}
      </div>
      <div className="rule flex shrink-0 items-center justify-between gap-3 border-t px-3 py-2">
        <span className="text-[12px] leading-tight opacity-65">
          {changed ? 'Only you see these changes' : 'The flat as it is'}
        </span>
        <button
          type="button"
          onClick={() => {
            closeChoices();
            reset('all');
          }}
          disabled={!changed}
          className="tap flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--paper)] px-3.5 text-[12.5px] font-medium transition-colors hover:border-[var(--ink-soft)] disabled:opacity-40"
          data-testid="menu-reset"
        >
          <OriginalIcon small />
          Reset all
        </button>
      </div>
    </>
  );

  return (
    <section
      className={[
        'panel flex flex-col overflow-hidden rounded-[16px]',
        compact ? 'w-full' : 'max-h-full w-[400px]',
      ].join(' ')}
      // On a phone, room above it for the view to show what is chosen.
      style={compact && open ? { maxHeight: 'min(56vh, 560px)' } : undefined}
      aria-label="Design"
      data-testid="design-menu"
      data-open={open}
    >
      {header}
      {open && (
        <div id="design-menu-body" className="flex min-h-0 flex-1 flex-col">
          {body}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

/** Tiles as wide as fit: four across on a phone and in the panel, three for furniture. */
function Grid({ wide = false, children }: { wide?: boolean; children: React.ReactNode }) {
  return (
    <ul
      className={[
        'grid gap-x-2.5 gap-y-3 p-3',
        wide ? 'grid-cols-[repeat(auto-fill,minmax(96px,1fr))]' : 'grid-cols-[repeat(auto-fill,minmax(72px,1fr))]',
      ].join(' ')}
    >
      {children}
    </ul>
  );
}

/** Choices side by side, all one width, the chosen one marked. */
function Segmented({
  label,
  options,
  value,
  onChange,
  testId,
}: {
  label: string;
  options: Array<[string, string]>;
  value: string;
  onChange: (value: string) => void;
  testId?: string;
}) {
  return (
    <div className="scroll-x px-3 pt-3">
      <div
        role="radiogroup"
        aria-label={label}
        className="grid min-w-full auto-cols-[minmax(max-content,1fr)] grid-flow-col gap-1.5"
        data-testid={testId}
      >
        {options.map(([option, text]) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={value === option}
            onClick={() => onChange(option)}
            className={[
              'tap min-h-9 rounded-full border px-3 text-[12.5px] font-medium transition-colors',
              value === option
                ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]'
                : 'border-[var(--line)] hover:border-[var(--ink-soft)]',
            ].join(' ')}
            data-scope={option}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

/** A picture with a word under it: the one control every tab is made of. */
function Choice({
  label,
  selected,
  changed,
  disabled,
  onClick,
  testId,
  data,
  children,
}: {
  label: string;
  selected?: boolean;
  /** Not as it was built: marked with the dot changes carry everywhere. */
  changed?: boolean;
  disabled?: boolean;
  onClick: () => void;
  testId?: string;
  data?: Record<string, string>;
  children: React.ReactNode;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-pressed={selected}
        className={['tap group flex w-full flex-col items-center gap-1.5', disabled ? 'opacity-35' : ''].join(' ')}
        data-testid={testId}
        {...data}
      >
        <span
          className={[
            'relative grid aspect-square w-full place-items-center overflow-hidden rounded-[12px] transition-shadow',
            'bg-[linear-gradient(160deg,#fffdf9,var(--paper-warm))]',
            selected
              ? 'shadow-[0_0_0_2px_var(--accent),var(--shadow-raised)]'
              : 'shadow-[0_0_0_1px_var(--line)] group-hover:shadow-[0_0_0_1px_var(--ink-soft)]',
          ].join(' ')}
        >
          {children}
          {selected && (
            <span
              aria-hidden="true"
              className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-full bg-[var(--accent)] text-[var(--paper)] shadow-[var(--shadow-raised)]"
            >
              <CheckIcon />
            </span>
          )}
          {changed && !selected && (
            <span className="absolute right-1.5 top-1.5">
              <Dot label="Changed" />
            </span>
          )}
        </span>
        <span
          className={[
            'line-clamp-2 h-[2.5em] w-full text-center text-[11.5px] leading-[1.25]',
            selected ? 'font-semibold' : '',
          ].join(' ')}
        >
          {label}
        </span>
      </button>
    </li>
  );
}

/** The mark of a change, as on the room strip. */
function Dot({ label }: { label?: string }) {
  return (
    <span
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--staged)]"
    />
  );
}

function Stripes({ colours }: { colours: string[] }) {
  return (
    <span className="flex h-full w-full" aria-hidden="true">
      {colours.map((c) => (
        <span key={c} className="h-full flex-1" style={{ background: c }} />
      ))}
    </span>
  );
}

/** A fitting's finish: its main colour, the worktop's as a band along the top. */
function FinishSwatch({ finish }: { finish: Finish }) {
  const [main, second] = finish.swatches;
  return (
    <span className="flex h-full w-full flex-col" aria-hidden="true">
      {second && <span className="h-[22%]" style={{ background: second }} />}
      <span className="flex-1" style={{ background: main }} />
    </span>
  );
}

function Thumbnail({ item }: { item: CatalogItem }) {
  return item.thumbnail ? (
    <img
      src={apiUrl(item.thumbnail)}
      alt=""
      loading="lazy"
      decoding="async"
      className="h-full w-full object-contain p-1.5"
    />
  ) : (
    <span className="px-1 text-center text-[11px] opacity-60">{item.name}</span>
  );
}

function Credit() {
  return (
    <p className="px-3 pb-3 text-[10.5px] leading-snug opacity-60">
      Furniture models: Amazon.com, Amazon Berkeley Objects,{' '}
      <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer" className="underline">
        CC BY 4.0
      </a>
      , simplified.
    </p>
  );
}

/** What can stand in one slot: the piece there now marked, what does not fit greyed out. */
function Alternatives({
  slot,
  room,
  current,
  onBack,
  onChoose,
  onClear,
}: {
  slot: Slot;
  room: Room;
  current?: CatalogItem;
  onBack: () => void;
  onChoose: (itemId: string) => void;
  onClear: () => void;
}) {
  const { viewer, catalog } = useStore();
  const kitId = viewer.styles[room.id];
  const options = useMemo(() => {
    const placed = viewer.items.flatMap((i) => {
      const s = i.roomId === room.id ? room.slots?.find((x) => x.id === i.slotId) : undefined;
      const item = catalog.furniture.find((f) => f.id === i.itemId);
      return s && item ? [{ slot: s, item }] : [];
    });
    return alternatives(slot, catalog.furniture, kitId, {
      ceilings: room.ceilings,
      base: baseFor(slot, placed) ?? 0,
    });
  }, [slot, room, kitId, viewer.items, catalog.furniture]);

  return (
    <div>
      {/* Pinned, so the way back is in sight however far the list scrolls. */}
      <div className="rule sticky top-0 z-10 flex items-center gap-2.5 border-b bg-[var(--paper)] px-3 py-2">
        <button
          type="button"
          onClick={onBack}
          className="tap flex min-h-9 shrink-0 items-center gap-1 rounded-full border border-[var(--line)] pl-2 pr-3.5 text-[12.5px] font-medium transition-colors hover:border-[var(--ink-soft)]"
          data-testid="alternatives-back"
        >
          <Chevron direction="left" />
          All furniture
        </button>
        <h3 className="min-w-0 truncate text-[13px] font-semibold">
          {categoryName(slot.category)} <span className="font-normal opacity-60">· {room.name}</span>
        </h3>
      </div>
      <Grid wide>
        <Choice
          label={slot.replaces?.length ? 'Original' : 'None'}
          selected={!current}
          onClick={onClear}
          testId="slot-clear"
        >
          <OriginalIcon />
        </Choice>
        {options.map(({ item, fits }) => (
          <Choice
            key={item.id}
            label={item.name}
            selected={current?.id === item.id}
            disabled={!fits}
            onClick={() => onChoose(item.id)}
            data={{ 'data-furniture-id': item.id }}
          >
            <Thumbnail item={item} />
          </Choice>
        ))}
      </Grid>
    </div>
  );
}

// ---- icons ----------------------------------------------------------------

const ICON = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
} as const;

/** As the flat was built: no change. */
function OriginalIcon({ small = false }: { small?: boolean }) {
  const size = small ? 14 : 22;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...ICON} className={small ? '' : 'opacity-55'}>
      <path d="M3 12a9 9 0 1 0 3-6.7" />
      <path d="M3 4v5h5" />
    </svg>
  );
}

function PaletteIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" {...ICON}>
      <path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.7-.8 1.7-1.6 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.9.7-1.6 1.6-1.6H16a5 5 0 0 0 5-5C21 6.4 17 3 12 3Z" />
      <circle cx="7.5" cy="11" r="1" fill="currentColor" />
      <circle cx="10" cy="7" r="1" fill="currentColor" />
      <circle cx="14.5" cy="7" r="1" fill="currentColor" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" {...ICON} className="opacity-40">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" {...ICON} strokeWidth={3}>
      <path d="m5 12.5 4.5 4.5L19 7.5" />
    </svg>
  );
}

function Chevron({ direction }: { direction: 'up' | 'down' | 'left' | 'right' }) {
  const turn = { up: 0, right: 90, down: 180, left: 270 }[direction];
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" {...ICON} strokeWidth={2.2} style={{ transform: `rotate(${turn}deg)` }}>
      <path d="m6 15 6-6 6 6" />
    </svg>
  );
}
