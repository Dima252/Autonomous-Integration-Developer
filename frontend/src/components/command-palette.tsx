"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Search } from "lucide-react";
import { cx } from "@/lib/format";
import { Kbd } from "./ui";

export interface PaletteAction {
  id: string;
  group: string;
  label: string;
  run: () => void;
}

/** ⌘K / Ctrl+K: run any action or send an example request. A native <dialog>, so focus
 * trapping and Escape come from the browser. */
export function CommandPalette({
  open,
  onClose,
  actions,
}: {
  open: boolean;
  onClose: () => void;
  actions: PaletteAction[];
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? actions.filter((a) => a.label.toLowerCase().includes(q) || a.group.toLowerCase().includes(q)) : actions;
  }, [actions, query]);

  const choose = (action: PaletteAction | undefined) => {
    if (!action) return;
    onClose();
    setQuery("");
    setActive(0);
    action.run();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(matches.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(matches[active]);
    }
  };

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && onClose()}
      aria-label="Commands"
      className="m-auto mt-[12vh] w-[min(560px,calc(100vw-32px))] overflow-hidden rounded-xl border border-border bg-panel p-0 text-text shadow-2xl backdrop:bg-black/50"
    >
      <div className="flex items-center gap-2 border-b border-border px-3">
        <Search size={16} className="text-faint" aria-hidden="true" />
        <label htmlFor="palette-input" className="sr-only">
          Search commands
        </label>
        <input
          id="palette-input"
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Type a command or an example…"
          className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-faint"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={matches[active] ? `palette-${matches[active].id}` : undefined}
        />
        <Kbd>Esc</Kbd>
      </div>
      <ul id="palette-list" role="listbox" className="max-h-80 overflow-y-auto p-1.5">
        {matches.length === 0 && <li className="px-3 py-6 text-center text-sm text-faint">No matches</li>}
        {matches.map((a, i) => {
          const header = i === 0 || matches[i - 1].group !== a.group;
          return (
            <li key={a.id} role="presentation">
              {header && <p className="px-2.5 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-faint">{a.group}</p>}
              <button
                id={`palette-${a.id}`}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(a)}
                className={cx(
                  "w-full truncate rounded-md px-2.5 py-2 text-left text-sm",
                  i === active ? "bg-accent-soft text-text" : "text-muted",
                )}
              >
                {a.label}
              </button>
            </li>
          );
        })}
      </ul>
    </dialog>
  );
}
