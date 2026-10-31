/**
 * The design menu: style, floor and furniture for the room you stand in, and
 * what every room has. A bottom sheet on a phone, collapsed to one line until
 * opened; a panel at the right on a big screen. It reads and changes the one
 * store the 3D taps and the assistant use, so all three always agree.
 */

import { useMemo, useState } from 'react';
import { apiUrl } from '../lib/api';
import { floorColor } from '../lib/floorColors';
import type { Room, Slot } from '../lib/manifest';
import { type CatalogItem, useStore } from '../lib/store';
import { alternatives, baseFor, kitReady, type StyleKit } from '../lib/styles';
import { FloorPanel, FurnitureTile } from './Panels';

export type DesignTab = 'style' | 'floor' | 'furniture' | 'room';

const TABS: Array<[DesignTab, string]> = [
  ['style', 'Style'],
  ['floor', 'Floor'],
  ['furniture', 'Furniture & lamps'],
  ['room', 'This room'],
];

/** How a slot's category reads in a list. */
const CATEGORY_NAMES: Record<string, string> = {
  sofa: 'Sofa',
  armchair: 'Armchair',
  coffee_table: 'Coffee table',
  side_table: 'Side table',
  rug: 'Rug',
  floor_lamp: 'Floor lamp',
  table_lamp: 'Table lamp',
  pendant_lamp: 'Pendant lamp',
  bed: 'Bed',
  nightstand: 'Nightstand',
  dresser: 'Dresser',
  dining_table: 'Dining table',
  dining_chair: 'Dining chair',
  desk: 'Desk',
  desk_chair: 'Desk chair',
  shelf: 'Shelf',
};

export const categoryName = (category: string) =>
  CATEGORY_NAMES[category] ?? category.replace(/_/g, ' ');

/** "203 × 94 × 87 cm": width, depth, height. */
export function sizeLabel(item: CatalogItem): string {
  const [w, h, d] = item.dimensionsM ?? [0, 0, 0];
  return `${Math.round(w * 100)} × ${Math.round(d * 100)} × ${Math.round(h * 100)} cm`;
}

interface DesignMenuProps {
  room: Room;
  /** A phone: the bottom sheet; otherwise the side panel. */
  compact: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function DesignMenu({ room, compact, open, onOpenChange }: DesignMenuProps) {
  const {
    manifest, viewer, catalog, selectedUid,
    applyStyle, swapPiece, setFloor, reset, goToRoom, selectItem,
  } = useStore();
  const [tab, setTab] = useState<DesignTab>('style');
  /** A slot chosen from a row, in the room it was chosen in. */
  const [picked, setPicked] = useState<{ roomId: string; slotId: string } | null>(null);

  const kits = useMemo(
    () => catalog.styles.filter((k) => kitReady(k, room.type, room.slots ?? [], catalog.furniture)),
    [catalog.styles, catalog.furniture, room],
  );
  const kit = catalog.styles.find((k) => k.id === viewer.styles[room.id]);
  const inRoom = viewer.items.filter((i) => i.roomId === room.id && i.slotId);
  const pieceIn = (slot: Slot) => {
    const placed = inRoom.find((i) => i.slotId === slot.id);
    return placed ? catalog.furniture.find((f) => f.id === placed.itemId) : undefined;
  };

  // The slot whose alternatives are listed: a kit's piece tapped in 3D, or
  // a row chosen here, in this room.
  const tapped = viewer.items.find((i) => i.uid === selectedUid && i.roomId === room.id);
  const choosing = tapped?.slotId ?? (picked?.roomId === room.id ? picked.slotId : null);
  const choosingSlot = room.slots?.find((s) => s.id === choosing);
  const setChoosing = (slotId: string | null) =>
    setPicked(slotId ? { roomId: room.id, slotId } : null);

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
        <span className="block truncate text-[14px] font-semibold tracking-tight">{room.name}</span>
        <span className="block truncate text-[12px] opacity-70">
          {kit ? kit.name : 'As it is'}
        </span>
      </span>
      <span aria-hidden="true" className="text-[16px] opacity-70">
        {compact ? (open ? '▾' : '▴') : open ? '›' : '‹'}
      </span>
    </button>
  );

  const body = choosingSlot ? (
    <Alternatives
      slot={choosingSlot}
      room={room}
      kit={kit}
      current={pieceIn(choosingSlot)}
      onBack={() => {
        setChoosing(null);
        selectItem(null);
      }}
      onChoose={(itemId) => swapPiece(room.id, choosingSlot.id, itemId)}
    />
  ) : (
    <>
      <div role="tablist" aria-label="Design" className="scroll-x rule flex gap-1 border-b px-3 py-2">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={[
              'tap min-h-11 shrink-0 rounded-[var(--radius-sm)] px-3 text-[13px] font-medium',
              tab === id ? 'bg-[var(--accent)] text-[var(--paper)]' : 'hover:bg-[var(--paper-warm)]',
            ].join(' ')}
            data-testid={`design-tab-${id}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'style' && (
          <StyleTab
            kits={kits}
            current={kit?.id}
            roomName={room.name}
            onApply={(id) => applyStyle([room.id], id)}
            onApplyAll={(id) => applyStyle('all', id)}
          />
        )}
        {tab === 'floor' && (
          <FloorPanel
            floors={catalog.floors}
            appliedFloorId={viewer.floors[room.id]}
            roomName={room.name}
            onApply={(floorId, allRooms) => setFloor(allRooms ? ['all'] : [room.id], floorId)}
            onClear={() => reset(room.id)}
          />
        )}
        {tab === 'furniture' && (
          <FurnitureTab room={room} pieceIn={pieceIn} onChoose={setChoosing} />
        )}
        {tab === 'room' && (
          <RoomTab
            room={room}
            rooms={manifest?.rooms ?? []}
            pieceIn={pieceIn}
            onChoose={setChoosing}
            onFloor={() => setTab('floor')}
            onGoTo={goToRoom}
          />
        )}
        <p className="px-4 pb-3 pt-1 text-[10.5px] leading-snug opacity-75">
          Furniture models: Amazon.com, Amazon Berkeley Objects,{' '}
          <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer" className="underline">
            CC BY 4.0
          </a>
          , simplified.
        </p>
      </div>
    </>
  );

  if (compact) {
    return (
      <section
        className="panel rule flex flex-col border-t"
        style={{ maxHeight: open ? '72vh' : undefined }}
        aria-label="Design"
        data-testid="design-menu"
        data-open={open}
      >
        {header}
        {open && (
          <div id="design-menu-body" className="rule flex min-h-0 flex-1 flex-col border-t">
            {body}
          </div>
        )}
      </section>
    );
  }
  return (
    <aside
      className="panel rule flex max-h-full w-[360px] flex-col rounded-[10px] border"
      aria-label="Design"
      data-testid="design-menu"
      data-open={open}
    >
      {header}
      {open && (
        <div id="design-menu-body" className="rule flex min-h-0 flex-1 flex-col border-t">
          {body}
        </div>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------

function StyleTab({
  kits,
  current,
  roomName,
  onApply,
  onApplyAll,
}: {
  kits: StyleKit[];
  current?: string;
  roomName: string;
  onApply: (id: string) => void;
  onApplyAll: (id: string) => void;
}) {
  if (!kits.length) {
    return (
      <p className="px-4 py-6 text-[13px] leading-relaxed opacity-75">
        No style fits the {roomName.toLowerCase()} yet: it has no places for furniture. Try the
        living room or the bedroom.
      </p>
    );
  }
  return (
    <div className="p-3">
      <ul className="grid gap-2">
        {kits.map((kit) => (
          <li key={kit.id}>
            <button
              type="button"
              onClick={() => onApply(kit.id)}
              aria-pressed={current === kit.id}
              className={[
                'tap w-full rounded-[9px] border p-3 text-left transition-colors',
                current === kit.id
                  ? 'border-[var(--accent)] bg-[var(--accent-soft)]'
                  : 'border-[var(--line)] hover:bg-[var(--paper-warm)]',
              ].join(' ')}
              data-style-id={kit.id}
            >
              <span className="flex items-center justify-between gap-3">
                <span className="text-[14px] font-semibold tracking-tight">{kit.name}</span>
                <span className="flex gap-1" aria-hidden="true">
                  {kit.swatches.map((c) => (
                    <span key={c} className="h-4 w-4 rounded-full border border-black/10" style={{ background: c }} />
                  ))}
                </span>
              </span>
              <span className="mt-1 block text-[12.5px] leading-snug opacity-75">
                {kit.description}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() => onApplyAll(current ?? kits[0].id)}
        className="btn tap mt-3 min-h-11 w-full px-3 text-[13px]"
        data-testid="apply-all-rooms"
      >
        Apply {kits.find((k) => k.id === current)?.name ?? kits[0].name} to all rooms
      </button>
    </div>
  );
}

function FurnitureTab({
  room,
  pieceIn,
  onChoose,
}: {
  room: Room;
  pieceIn: (slot: Slot) => CatalogItem | undefined;
  onChoose: (slotId: string) => void;
}) {
  const slots = room.slots ?? [];
  if (!slots.length) {
    return (
      <p className="px-4 py-6 text-[13px] opacity-75">
        The {room.name.toLowerCase()} has no places for furniture.
      </p>
    );
  }
  return (
    <ul className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3">
      {slots.map((slot) => {
        const piece = pieceIn(slot);
        return (
          <li key={slot.id}>
            <button
              type="button"
              onClick={() => onChoose(slot.id)}
              className="tap w-full text-left"
              data-slot-id={slot.id}
            >
              {piece ? <FurnitureTile item={piece} /> : <EmptyTile />}
              <span className="mt-1 block text-[12px] font-medium">{categoryName(slot.category)}</span>
              <span className="block truncate text-[11px] opacity-80">{piece?.name ?? 'Choose one'}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function EmptyTile() {
  return (
    <span className="flex aspect-square w-full items-center justify-center rounded-md border border-dashed border-[var(--line)] text-[20px] opacity-50">
      +
    </span>
  );
}

function RoomTab({
  room,
  rooms,
  pieceIn,
  onChoose,
  onFloor,
  onGoTo,
}: {
  room: Room;
  rooms: Room[];
  pieceIn: (slot: Slot) => CatalogItem | undefined;
  onChoose: (slotId: string) => void;
  onFloor: () => void;
  onGoTo: (roomId: string) => void;
}) {
  const { manifest, viewer, catalog } = useStore();
  const floor = catalog.floors.find((f) => f.id === viewer.floors[room.id]);
  const filled = new Set(
    (room.slots ?? []).filter((s) => pieceIn(s)).flatMap((s) => s.replaces ?? []),
  );
  // The flat's own furniture: listed as it is, unless a kit's piece stands in for it.
  const originals = (manifest?.items ?? []).filter(
    (i) => i.roomId === room.id && ['sofa', 'bed', 'chair', 'tv'].includes(i.label) && !filled.has(i.id),
  );
  return (
    <div>
      <ul className="divide-y divide-[var(--line)]">
        <Row
          thumb={<span className="block h-full w-full" style={{ background: floor ? floorColor(floor) : 'var(--paper-warm)' }} />}
          title={floor?.name ?? 'Original floor'}
          detail="Floor"
          onClick={onFloor}
        />
        {(room.slots ?? []).map((slot) => {
          const piece = pieceIn(slot);
          return (
            <Row
              key={slot.id}
              thumb={piece?.thumbnail ? <img src={apiUrl(piece.thumbnail)} alt="" loading="lazy" className="h-full w-full object-contain" /> : null}
              title={piece?.name ?? `No ${categoryName(slot.category).toLowerCase()}`}
              detail={piece ? `${categoryName(slot.category)} · ${sizeLabel(piece)}` : 'Tap to choose one'}
              onClick={() => onChoose(slot.id)}
            />
          );
        })}
        {originals.map((item) => (
          <Row
            key={item.id}
            thumb={null}
            title={`${item.label[0].toUpperCase()}${item.label.slice(1)}`}
            detail={`Original · ${Math.round(item.box.size[0] * 100)} × ${Math.round(item.box.size[2] * 100)} × ${Math.round(item.box.size[1] * 100)} cm`}
          />
        ))}
      </ul>
      {rooms.length > 1 && (
        <div className="rule border-t px-4 pb-3 pt-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] opacity-60">Other rooms</h3>
          <ul className="mt-1.5">
            {rooms
              .filter((r) => r.id !== room.id)
              .map((r) => {
                const kit = catalog.styles.find((k) => k.id === viewer.styles[r.id]);
                const f = catalog.floors.find((x) => x.id === viewer.floors[r.id]);
                return (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => onGoTo(r.id)}
                      className="tap flex min-h-11 w-full items-center justify-between gap-3 text-left text-[13px] hover:underline"
                      data-room-link={r.id}
                    >
                      <span className="font-medium">{r.name}</span>
                      <span className="truncate text-[12px] opacity-80">
                        {kit?.name ?? 'As it is'} · {f?.name ?? 'original floor'}
                      </span>
                    </button>
                  </li>
                );
              })}
          </ul>
        </div>
      )}
    </div>
  );
}

function Row({
  thumb,
  title,
  detail,
  onClick,
}: {
  thumb: React.ReactNode;
  title: string;
  detail: string;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span className="h-11 w-11 shrink-0 overflow-hidden rounded-md border border-[var(--line)] bg-[var(--paper-warm)]">
        {thumb}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium">{title}</span>
        <span className="measure block truncate text-[11.5px] opacity-80">{detail}</span>
      </span>
      {onClick && <span aria-hidden="true" className="opacity-50">›</span>}
    </>
  );
  return (
    <li>
      {onClick ? (
        <button type="button" onClick={onClick} className="tap flex min-h-14 w-full items-center gap-3 px-4 py-1.5 text-left hover:bg-[var(--paper-warm)]">
          {content}
        </button>
      ) : (
        <div className="flex min-h-14 items-center gap-3 px-4 py-1.5">{content}</div>
      )}
    </li>
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
    const base = baseFor(slot, placed);
    return alternatives(slot, catalog.furniture, kit?.id, {
      ceilings: room.ceilings,
      base: base ?? 0,
    });
  }, [slot, room, kit, viewer.items, catalog.furniture]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="rule flex items-center gap-2 border-b px-2 py-1">
        <button
          type="button"
          onClick={onBack}
          className="tap min-h-11 rounded-[var(--radius-sm)] px-3 text-[13px] hover:bg-[var(--paper-warm)]"
          data-testid="alternatives-back"
        >
          ‹ Back
        </button>
        <h3 className="text-[13px] font-semibold">{categoryName(slot.category)}</h3>
      </div>
      <ul className="min-h-0 flex-1 divide-y divide-[var(--line)] overflow-y-auto">
        {options.map(({ item, fits }) => (
          <li key={item.id}>
            <button
              type="button"
              onClick={() => fits && onChoose(item.id)}
              disabled={!fits}
              aria-pressed={current?.id === item.id}
              className={[
                'tap flex min-h-14 w-full items-center gap-3 px-4 py-1.5 text-left',
                fits ? 'hover:bg-[var(--paper-warm)]' : 'opacity-45',
                current?.id === item.id ? 'bg-[var(--accent-soft)]' : '',
              ].join(' ')}
              data-furniture-id={item.id}
            >
              <span className="h-12 w-12 shrink-0 overflow-hidden rounded-md border border-[var(--line)] bg-[var(--paper-warm)]">
                {item.thumbnail && (
                  <img src={apiUrl(item.thumbnail)} alt="" loading="lazy" className="h-full w-full object-contain" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{item.name}</span>
                <span className="measure block text-[11.5px] opacity-80">
                  {sizeLabel(item)}
                  {fits ? '' : ' · does not fit here'}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
