/**
 * The design menu: style, floor and furniture for the room you stand in,
 * and the finishes of the flat's kitchen and wardrobe. A bottom sheet on a
 * phone, collapsed to one line until opened; a panel at the right on a big
 * screen. Pictures first, a word under each: it reads and changes the one
 * store the 3D taps and the assistant use, so all three always agree.
 */

import { useMemo, useState } from 'react';
import { apiUrl } from '../lib/api';
import { type Finish, type Fitting, FITTINGS } from '../lib/finishes';
import { floorColor } from '../lib/floorColors';
import type { Room, Slot } from '../lib/manifest';
import { type CatalogItem, useStore } from '../lib/store';
import { alternatives, baseFor, kitReady, type StyleKit } from '../lib/styles';

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

interface DesignMenuProps {
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
    applyStyle, swapPiece, setFloor, setFinish, reset, selectItem,
  } = useStore();
  const [chosenTab, setTab] = useState<DesignTab>('style');
  const [allRooms, setAllRooms] = useState(false);
  /** A slot chosen from the furniture tiles, in the room it was chosen in. */
  const [picked, setPicked] = useState<{ roomId: string; slotId: string } | null>(null);

  const slots = useMemo(() => room.slots ?? [], [room.slots]);
  const kits = useMemo(
    () => catalog.styles.filter((k) => kitReady(k, room.type, slots, catalog.furniture)),
    [catalog.styles, catalog.furniture, room.type, slots],
  );
  const kit = catalog.styles.find((k) => k.id === viewer.styles[room.id]);

  // Only what this room can take: no style or furniture tab in a hall.
  const tabs: Array<[DesignTab, string]> = [
    ...(kits.length ? [['style', 'Style'] as [DesignTab, string]] : []),
    ['floor', 'Floor'],
    ...(slots.length ? [['furniture', 'Furniture'] as [DesignTab, string]] : []),
    ...FITTINGS.filter((f) => fittings.includes(f.id)).map((f) => [f.id, f.name] as [DesignTab, string]),
  ];
  const tab = tabs.some(([id]) => id === chosenTab) ? chosenTab : tabs[0][0];

  const inRoom = viewer.items.filter((i) => i.roomId === room.id && i.slotId);
  const pieceIn = (slot: Slot) => {
    const placed = inRoom.find((i) => i.slotId === slot.id);
    return placed ? catalog.furniture.find((f) => f.id === placed.itemId) : undefined;
  };

  // The slot whose alternatives are shown: a kit's piece tapped in 3D, or a
  // tile chosen here, in this room.
  const tapped = viewer.items.find((i) => i.uid === selectedUid && i.roomId === room.id);
  const choosing = tapped?.slotId ?? (picked?.roomId === room.id ? picked.slotId : null);
  const choosingSlot = slots.find((s) => s.id === choosing);
  const rooms = manifest?.rooms ?? [];
  const everywhere = allRooms && rooms.length > 1;

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
        <span className="block truncate text-[14px] font-semibold tracking-tight">Design · {room.name}</span>
        <span className="block truncate text-[12px] opacity-70">{kit ? kit.name : 'As it is'}</span>
      </span>
      <span aria-hidden="true" className="text-[16px] opacity-70">
        {compact ? (open ? '▾' : '▴') : open ? '›' : '‹'}
      </span>
    </button>
  );

  const panel = () => {
    if (tab === 'style') {
      return (
        <Grid columns={4}>
          <Choice
            label="As it is"
            selected={!kit}
            onClick={() => (everywhere ? rooms : [room]).forEach((r) => reset(r.id))}
            testId="style-none"
          >
            <Original />
          </Choice>
          {kits.map((k) => (
            <Choice
              key={k.id}
              label={k.name}
              selected={kit?.id === k.id}
              onClick={() => applyStyle(everywhere ? 'all' : [room.id], k.id)}
              data={{ 'data-style-id': k.id }}
            >
              <Stripes colours={k.swatches} />
            </Choice>
          ))}
        </Grid>
      );
    }
    if (tab === 'floor') {
      const applied = viewer.floors[room.id];
      return (
        <Grid columns={4}>
          <Choice
            label="Original"
            selected={!applied}
            onClick={() => setFloor(everywhere ? ['all'] : [room.id], null)}
            testId="floor-original"
          >
            <Original />
          </Choice>
          {catalog.floors.map((floor) => (
            <Choice
              key={floor.id}
              label={floor.name}
              selected={floor.id === applied}
              onClick={() => setFloor(everywhere ? ['all'] : [room.id], floor.id)}
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
      );
    }
    if (tab === 'furniture') {
      return (
        <Grid columns={3}>
          {slots.map((slot) => {
            const piece = pieceIn(slot);
            return (
              <Choice
                key={slot.id}
                label={categoryName(slot.category)}
                onClick={() => setPicked({ roomId: room.id, slotId: slot.id })}
                data={{ 'data-slot-id': slot.id }}
              >
                {piece ? <Thumbnail item={piece} /> : <span className="text-[22px] opacity-40">+</span>}
              </Choice>
            );
          })}
        </Grid>
      );
    }
    const own = viewer.finishes[tab];
    return (
      <Grid columns={4}>
        <Choice label="Original" selected={!own} onClick={() => setFinish(tab, null)} testId={`${tab}-original`}>
          <Original />
        </Choice>
        {catalog.finishes
          .filter((f) => f.fitting === tab)
          .map((finish) => (
            <Choice
              key={finish.id}
              label={finish.name}
              selected={own === finish.id}
              onClick={() => setFinish(tab, finish.id)}
              data={{ 'data-finish-id': finish.id }}
            >
              <FinishSwatch finish={finish} />
            </Choice>
          ))}
      </Grid>
    );
  };

  const body = choosingSlot ? (
    <Alternatives
      slot={choosingSlot}
      room={room}
      kit={kit}
      current={pieceIn(choosingSlot)}
      onBack={() => {
        setPicked(null);
        selectItem(null);
      }}
      onChoose={(itemId) => swapPiece(room.id, choosingSlot.id, itemId)}
    />
  ) : (
    <>
      <div className="rule border-b px-2 py-1.5">
        <div role="tablist" aria-label="Design" className="scroll-x flex gap-1">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
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
        {(tab === 'style' || tab === 'floor') && rooms.length > 1 && (
          <label className="flex cursor-pointer items-center justify-end gap-1.5 px-3 pt-2.5 text-[12px]">
            <input
              type="checkbox"
              checked={allRooms}
              onChange={(e) => setAllRooms(e.target.checked)}
              className="h-4 w-4 accent-[var(--accent)]"
              data-testid="all-rooms"
            />
            All rooms
          </label>
        )}
        {panel()}
        {(tab === 'style' || tab === 'furniture') && <Credit />}
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
        style={{ maxHeight: open ? '64vh' : undefined }}
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

function Grid({ columns, children }: { columns: 3 | 4; children: React.ReactNode }) {
  return (
    <ul className={['grid gap-x-2 gap-y-3 p-3', columns === 4 ? 'grid-cols-4' : 'grid-cols-3'].join(' ')}>
      {children}
    </ul>
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

/** The flat as it was built: no change. */
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

function Alternatives({
  slot,
  room,
  kit,
  current,
  onBack,
  onChoose,
}: {
  slot: Slot;
  room: Room;
  kit?: StyleKit;
  current?: CatalogItem;
  onBack: () => void;
  onChoose: (itemId: string) => void;
}) {
  const { viewer, catalog } = useStore();
  const options = useMemo(() => {
    const placed = viewer.items.flatMap((i) => {
      const s = i.roomId === room.id ? room.slots?.find((x) => x.id === i.slotId) : undefined;
      const item = catalog.furniture.find((f) => f.id === i.itemId);
      return s && item ? [{ slot: s, item }] : [];
    });
    return alternatives(slot, catalog.furniture, kit?.id, {
      ceilings: room.ceilings,
      base: baseFor(slot, placed) ?? 0,
    });
  }, [slot, room, kit, viewer.items, catalog.furniture]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="rule flex items-center gap-2 border-b px-2 py-1.5">
        <button
          type="button"
          onClick={onBack}
          className="tap min-h-10 rounded-full px-3 text-[13px] hover:bg-[var(--paper-warm)]"
          data-testid="alternatives-back"
        >
          ‹ Back
        </button>
        <h3 className="text-[13px] font-semibold">{categoryName(slot.category)}</h3>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Grid columns={3}>
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
        <Credit />
      </div>
    </div>
  );
}
