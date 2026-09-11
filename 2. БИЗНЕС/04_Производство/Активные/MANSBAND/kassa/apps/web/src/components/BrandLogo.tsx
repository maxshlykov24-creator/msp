import logoFull from "../assets/logo-full.png";

/** Белый знак на прозрачном фоне. В светлой теме инвертируется в чёрный. */
export function BrandLogo({ className = "" }: { className?: string }) {
  return (
    <img
      src={logoFull}
      alt="MANSBAND"
      className={`brand-logo ${className}`.trim()}
      draggable={false}
    />
  );
}
