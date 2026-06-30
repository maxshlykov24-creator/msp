import type { ButtonHTMLAttributes, ReactNode } from "react";
import { getStageStyle } from "../lib/stageColors";

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
      {hint && <div className="text-[12px] text-mute/70 mt-1">{hint}</div>}
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

/** Бейдж этапа amoCRM — цвета как в воронке, приглушённые под тёмный UI */
export function StageBadge({ stage }: { stage: string }) {
  const s = getStageStyle(stage);
  return (
    <span
      className="chip border font-medium tracking-[0.01em]"
      style={{
        backgroundColor: s.bg,
        color: s.text,
        borderColor: s.ring,
      }}
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
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  title?: string;
  wide?: boolean;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-ink-950/80 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className={`relative card p-0 w-full ${wide ? "max-w-3xl" : "max-w-lg"} max-h-[90vh] overflow-hidden flex flex-col`}
      >
        {title && (
          <div className="px-6 py-4 border-b border-ink-700 flex items-center justify-between">
            <h3 className="text-lg font-bold text-white">{title}</h3>
            <button onClick={onClose} className="text-mute hover:text-white text-xl leading-none">
              ×
            </button>
          </div>
        )}
        <div className="overflow-y-auto p-6">{children}</div>
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
