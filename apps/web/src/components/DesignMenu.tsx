/**
 * The design menu: a style for the flat, floors, every piece of furniture,
 * and the finishes of the kitchen and the wardrobe. A bottom sheet on a
 * phone, collapsed to one line until opened; a panel at the right on a big
 * screen. The same tabs wherever you stand, pictures with a word under
 * each, choices opening inside their tab, and one button back to the
 * original. It reads and changes the one store the 3D taps and the
 * assistant use, so all three always agree.
 */

import { useMemo, useState } from 'react';
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

/** A slot being chosen for, in its room. */
interface Picked {
  roomId: string;
  slotId: string;
}

interface DesignMenuProps {
  /** The room you stand in: what a floor is laid in unless it is the whole flat. */
  room: Room;
  /** The fittings this flat has finishes for (finishes.fittingsIn). */
  fittings: Fitting[];
  /** A phone: the bottom sheet; otherwise the side panel. */
  compact: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function DesignMenu({ room, fittings, compact, open, onOpenChange }: DesignMenuProps) {
  const {
    manifest, viewer, catalog, selectedUid,
    applyStyle, removeStyle, swapPiece, clearSlot, setFloor, setFinish, reset, selectItem, goToRoom,
  } = useStore();
  const [tab, setTab] = useState<DesignTab>('style');
  const [picked, setPicked] = useState<Picked | null>(null);
  const [wholeFlat, setWholeFlat] = useState(false);

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
      className="tap flex min-h-12 w-full items-center justify-between gap-3 px-4 text-left"
      data-testid="design-menu-toggle"
    >
      <span className="min-w-0">
        <span className="block text-[15px] font-semibold tracking-tight">Design</span>
        <span className="block truncate text-[12px] opacity-70">
          {style ? style.name : changed ? 'Your changes' : 'Original'}
        </span>
      </span>
      <span className="flex items-center gap-2 text-[12px] font-medium opacity-80">
        {open ? 'Close' : 'Open'}
        <span aria-hidden="true" className="text-[15px]">
          {compact ? (open ? '▾' : '▴') : open ? '›' : '‹'}
        </span>
      </span>
    </button>
  );

  const styleTab = (
    <Grid>
      <Choice label="None" selected={!styleIds.length} onClick={removeStyle} testId="style-none">
        <Original />
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

  const floorScope = wholeFlat ? ['all'] : [room.id];
  const floorTab = (
    <>
      {rooms.length > 1 && (
        <Segmented
          options={[
            [false, room.name],
            [true, 'Whole flat'],
          ]}
          value={wholeFlat}
          onChange={setWholeFlat}
        />
      )}
      <Grid>
        <Choice
          label="Original"
          selected={!viewer.floors[room.id]}
          onClick={() => setFloor(floorScope, null)}
          testId="floor-original"
        >
          <Original />
        </Choice>
        {catalog.floors.map((floor) => (
          <Choice
            key={floor.id}
            label={floor.name}
            selected={floor.id === viewer.floors[room.id]}
            onClick={() => setFloor(floorScope, floor.id)}
            data={{ 'data-floor-id': floor.id }}
          >
            <span
              className="block h-full w-full"
              style={{
                background: floor.thumbnail ? `center/cover url(${apiUrl(floor.thumbnail)})` : floorColor(floor),
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
            <h3 className="px-3 pt-3 text-[12px] font-semibold opacity-70">{r.name}</h3>
            <Grid>
              {(r.slots ?? []).map((slot) => {
                const piece = pieceIn(r.id, slot);
                return (
                  <Choice
                    key={slot.id}
                    label={categoryName(slot.category)}
                    onClick={() => {
                      // Walk there, to see what you choose.
                      if (r.id !== room.id) goToRoom(r.id);
                      setPicked({ roomId: r.id, slotId: slot.id });
                    }}
                    data={{ 'data-slot-id': slot.id }}
                  >
                    {piece ? <Thumbnail item={piece} /> : <span className="text-[24px] opacity-40">+</span>}
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
        <Original />
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
      <div className="rule border-b px-2 py-1.5">
        <div role="tablist" aria-label="Design" className="scroll-x flex gap-1">
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
                'tap min-h-10 shrink-0 rounded-full px-3.5 text-[13px] font-medium transition-colors',
                tab === id ? 'bg-[var(--accent)] text-[var(--paper)]' : 'hover:bg-[var(--paper-warm)]',
              ].join(' ')}
              data-testid={`design-tab-${id}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div role="tabpanel" className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'style' && styleTab}
        {tab === 'floor' && floorTab}
        {tab === 'furniture' && furnitureTab}
        {(tab === 'kitchen' || tab === 'wardrobe') && fittingTab(tab)}
        {(tab === 'style' || tab === 'furniture') && <Credit />}
      </div>
      <div className="rule flex items-center justify-between gap-2 border-t px-3 py-2">
        <span className="text-[12px] opacity-70">
          {changed ? 'Only you see these changes.' : 'This is the flat as it is.'}
        </span>
        <button
          type="button"
          onClick={() => {
            closeChoices();
            reset('all');
          }}
          disabled={!changed}
          className="btn tap min-h-10 shrink-0 px-4 text-[13px] font-medium disabled:opacity-40"
          data-testid="menu-reset"
        >
          ↺ Reset all
        </button>
      </div>
    </>
  );

  const inner = open && (
    <div id="design-menu-body" className="rule flex min-h-0 flex-1 flex-col border-t">
      {body}
    </div>
  );
  if (compact) {
    return (
      <section
        className="panel rule flex flex-col border-t"
        style={{ maxHeight: open ? '60vh' : undefined }}
        aria-label="Design"
        data-testid="design-menu"
        data-open={open}
      >
        {header}
        {inner}
      </section>
    );
  }
  return (
    <aside
      className="panel rule flex max-h-full w-[440px] flex-col rounded-[12px] border"
      aria-label="Design"
      data-testid="design-menu"
      data-open={open}
    >
      {header}
      {inner}
    </aside>
  );
}

// ---------------------------------------------------------------------------

function Grid({ columns = 4, children }: { columns?: 3 | 4; children: React.ReactNode }) {
  return (
    <ul className={['grid gap-x-2 gap-y-3 p-3', columns === 4 ? 'grid-cols-4' : 'grid-cols-3'].join(' ')}>
      {children}
    </ul>
  );
}

/** Two or more choices side by side, the chosen one filled. */
function Segmented<T>({
  options,
  value,
  onChange,
}: {
  options: Array<[T, string]>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex gap-1 px-3 pt-3" role="radiogroup">
      {options.map(([option, label]) => (
        <button
          key={label}
          type="button"
          role="radio"
          aria-checked={value === option}
          onClick={() => onChange(option)}
          className={[
            'tap min-h-10 flex-1 truncate rounded-full border px-3 text-[13px] font-medium transition-colors',
            value === option
              ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
              : 'border-[var(--line)] hover:bg-[var(--paper-warm)]',
          ].join(' ')}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** A picture with a word under it: the one control every tab is made of. */
function Choice({
  label,
  selected,
  disabled,
  onClick,
  testId,
  data,
  children,
}: {
  label: string;
  selected?: boolean;
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
        className={['tap group block w-full text-left', disabled ? 'opacity-40' : ''].join(' ')}
        data-testid={testId}
        {...data}
      >
        <span
          className={[
            'flex aspect-square w-full items-center justify-center overflow-hidden rounded-[9px] bg-[var(--paper-warm)] transition-shadow',
            selected
              ? 'shadow-[0_0_0_2px_var(--accent)]'
              : 'shadow-[0_0_0_1px_var(--line)] group-hover:shadow-[0_0_0_1px_var(--ink)]',
          ].join(' ')}
        >
          {children}
        </span>
        <span className={['mt-1 line-clamp-2 block text-[12px] leading-tight', selected ? 'font-semibold' : ''].join(' ')}>
          {label}
        </span>
      </button>
    </li>
  );
}

/** As the flat was built: no change. */
function Original() {
  return (
    <span aria-hidden="true" className="text-[22px] opacity-55">
      ↺
    </span>
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
      className="h-full w-full object-contain"
    />
  ) : (
    <span className="text-[11px] opacity-60">{item.name}</span>
  );
}

function Credit() {
  return (
    <p className="px-3 pb-3 text-[10.5px] leading-snug opacity-70">
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
      <div className="rule sticky top-0 z-10 flex items-center gap-2 border-b bg-[var(--paper)] px-2 py-1.5">
        <button
          type="button"
          onClick={onBack}
          className="tap min-h-10 shrink-0 rounded-full border border-[var(--line)] px-3.5 text-[13px] font-medium hover:bg-[var(--paper-warm)]"
          data-testid="alternatives-back"
        >
          ‹ All furniture
        </button>
        <h3 className="truncate text-[13px] font-semibold">
          {categoryName(slot.category)} <span className="font-normal opacity-60">· {room.name}</span>
        </h3>
      </div>
      <Grid columns={3}>
        <Choice
          label={slot.replaces?.length ? 'Original' : 'None'}
          selected={!current}
          onClick={onClear}
          testId="slot-clear"
        >
          <Original />
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
