import type { Intent } from "./types";

export function usd(value: number | undefined): string {
  if (value === undefined) return "";
  if (value === 0) return "$0";
  if (value < 0.001) return `$${value.toFixed(5)}`;
  if (value < 1) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

export function ms(value: number | undefined): string {
  if (value === undefined) return "";
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

export function clock(elapsedMs: number): string {
  const s = Math.max(0, Math.floor(elapsedMs / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export const INTENT_LABELS: Record<Intent, string> = {
  chitchat: "Small talk",
  docs_question: "Docs question",
  integration_task: "Integration task",
  followup_edit: "Edit",
  clarify: "Needs detail",
};

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
