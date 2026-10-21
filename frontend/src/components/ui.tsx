"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { CircleCheck, CircleX, LoaderCircle, Circle, MinusCircle } from "lucide-react";
import { cx } from "@/lib/format";

type ButtonVariant = "primary" | "secondary" | "ghost";

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: "sm" | "md" }) {
  return (
    <button
      {...props}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-2.5 text-xs" : "h-9 px-3 text-sm",
        variant === "primary" && "bg-accent text-on-accent hover:bg-accent-strong",
        variant === "secondary" && "border border-border bg-raised text-text hover:bg-hover",
        variant === "ghost" && "text-muted hover:bg-hover hover:text-text",
        className,
      )}
    />
  );
}

export function IconButton({
  label,
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode }) {
  return (
    <button
      {...props}
      aria-label={label}
      title={label}
      className={cx(
        "inline-flex h-9 w-9 items-center justify-center rounded-md text-muted transition-colors",
        "hover:bg-hover hover:text-text disabled:opacity-50",
        className,
      )}
    >
      {children}
    </button>
  );
}

type Tone = "neutral" | "accent" | "ok" | "fail" | "warn";

const TONES: Record<Tone, string> = {
  neutral: "border-border bg-raised text-muted",
  accent: "border-transparent bg-accent-soft text-accent",
  ok: "border-transparent bg-ok-soft text-ok",
  fail: "border-transparent bg-fail-soft text-fail",
  warn: "border-transparent bg-warn-soft text-warn",
};

export function Badge({
  tone = "neutral",
  className,
  children,
  title,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex h-6 shrink-0 items-center gap-1 rounded-full border px-2 text-[11px] font-medium leading-none",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-border bg-raised px-1.5 py-0.5 font-mono text-[10px] text-muted">
      {children}
    </kbd>
  );
}

export type Status = "idle" | "active" | "done" | "failed" | "pass" | "fail" | "skip";

/** A status icon that never relies on color alone. */
export function StatusIcon({ status, size = 16 }: { status: Status; size?: number }) {
  switch (status) {
    case "active":
      return <LoaderCircle size={size} className="animate-spin text-accent" aria-label="running" />;
    case "done":
    case "pass":
      return <CircleCheck size={size} className="text-ok" aria-label="passed" />;
    case "failed":
    case "fail":
      return <CircleX size={size} className="text-fail" aria-label="failed" />;
    case "skip":
      return <MinusCircle size={size} className="text-faint" aria-label="skipped" />;
    default:
      return <Circle size={size} className="text-faint" aria-label="not started" />;
  }
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: ReactNode }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-lg border border-border bg-raised p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors",
            value === o.value ? "bg-panel text-text shadow-sm" : "text-muted hover:text-text",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
