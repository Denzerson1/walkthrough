/** The bottom sheet the assistant opens in. */

interface SheetProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

export function Sheet({ title, onClose, children }: SheetProps) {
  return (
    <section
      className="panel rule flex max-h-[58vh] flex-col border-t [@media(max-height:500px)]:max-h-[calc(100vh-8.5rem)]"
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
          className="tap -mr-1 px-2 py-1 text-[18px] leading-none opacity-70 hover:opacity-100"
        >
          ×
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </section>
  );
}
