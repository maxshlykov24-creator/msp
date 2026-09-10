import type { ButtonHTMLAttributes, ReactNode } from "react";
import { getStageGroup } from "../lib/stageColors";

export { Select, opts } from "./Select";
export type { SelectGroup, SelectOption } from "./Select";

type Variant = "primary" | "ghost" | "outline" | "danger" | "subtle";

export function Button({
  variant = "primary",
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg font-semibold text-[14px] px-4 py-2.5 transition active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed";
  const styles: Record<Variant, string> = {
    primary: "bg-gold text-ink-950 hover:bg-gold-soft shadow-glow",
    ghost: "text-mute-soft hover:bg-ink-800",
    outline: "border border-ink-600 text-mute-soft hover:border-gold/50 hover:text-white",
    danger: "border border-white/30 text-white hover:bg-white/10",
    subtle: "bg-ink-700 text-white hover:bg-ink-600",
  };
  return (
    <button className={`${base} ${styles[variant]} ${className}`} {...rest}>
      {children}
    </button>
  );
}

export function Card({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <div className={`card p-5 ${className}`}>{children}</div>;
}

export function Field({
  label,
  children,
  hint,
  required,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  required?: boolean;
}) {
  return (
    <label className="block">
      <div className="field-label flex items-center gap-1">
        {label}
        {required && <span className="text-gold">*</span>}
      </div>
      {children}
      {hint && <div className="hint-only text-[12px] text-mute/70 mt-1">{hint}</div>}
    </label>
  );
}

// Монохромная система бейджей: иерархия задаётся «заливкой», а не цветом.
const toneMap: Record<string, string> = {
  gold: "bg-white/15 text-white",
  green: "bg-white text-ink-950 font-semibold", // успех/готово — самый «плотный»
  red: "border border-ink-500 text-mute-soft bg-transparent", // провал/негатив — контур
  blue: "bg-white/10 text-mute-soft",
  gray: "bg-ink-600 text-mute-soft",
  amber: "bg-white/12 text-white/90", // в работе
};

export function Badge({
  tone = "gray",
  children,
}: {
  tone?: keyof typeof toneMap;
  children: ReactNode;
}) {
  return <span className={`chip ${toneMap[tone]}`}>{children}</span>;
}

/** Бейдж этапа amoCRM — палитра в CSS, светлая тема читается отдельно. */
export function StageBadge({ stage, className = "" }: { stage: string; className?: string }) {
  const group = getStageGroup(stage);
  return (
    <span
      title={stage}
      className={`chip stage-tone stage-tone-${group} font-medium tracking-[0.01em] max-w-full truncate ${className}`.trim()}
    >
      {stage}
    </span>
  );
}

export function Modal({
  open,
  onClose,
  children,
  title,
  wide,
  /** Почти на весь экран — карточка заявки из задач. */
  xl,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  title?: string;
  wide?: boolean;
  xl?: boolean;
}) {
  if (!open) return null;
  const width = xl ? "max-w-5xl" : wide ? "max-w-3xl" : "max-w-lg";
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-3 sm:p-4">
      <div
        className="absolute inset-0 bg-ink-950/80 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className={`relative z-[81] card p-0 w-full ${width} ${xl ? "max-h-[94vh]" : "max-h-[90vh]"} overflow-hidden flex flex-col`}
      >
        {title && (
          <div className="px-6 py-4 border-b border-ink-700 flex items-center justify-between shrink-0">
            <h3 className="text-lg font-bold text-white">{title}</h3>
            <button onClick={onClose} className="text-mute hover:text-white text-xl leading-none">
              ×
            </button>
          </div>
        )}
        <div className={`overflow-y-auto ${xl ? "p-4 sm:p-5" : "p-6"}`}>{children}</div>
      </div>
    </div>
  );
}

export function StatTile({
  label,
  value,
  tone = "gold",
  sub,
}: {
  label: string;
  value: string;
  tone?: keyof typeof toneMap;
  sub?: string;
}) {
  const accent: Record<string, string> = {
    gold: "text-white",
    green: "text-white",
    red: "text-white",
    blue: "text-white",
    gray: "text-white",
    amber: "text-white",
  };
  return (
    <div className="card p-4">
      <div className="field-label">{label}</div>
      <div className={`text-2xl font-extrabold ${accent[tone]}`}>{value}</div>
      {sub && <div className="text-[12px] text-mute mt-1">{sub}</div>}
    </div>
  );
}
