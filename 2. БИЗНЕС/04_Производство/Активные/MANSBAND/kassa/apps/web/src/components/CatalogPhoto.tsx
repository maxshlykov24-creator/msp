import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { SuitModel } from "@kassa/shared";

type PhotoProps = { name: string; suit?: SuitModel; groupNames?: string[] };
type PhotoIndex = {
  products: Record<string, Record<string, Record<string, string>>>;
  suits: Record<string, string>;
};

const normalize = (value: string | null) =>
  (value ?? "").normalize("NFC").trim().toLowerCase().replace(/\s+/g, " ");
const dimension = /^(?:[0-9]{1,3}(?:[./–-][0-9]{1,3})?|[2-9]?x{0,4}[sl]|m|one size|onesize)$/i;

function productAsset(index: PhotoIndex, name: string) {
  const label = normalize(name);
  const start = label.lastIndexOf(" (");
  if (start < 0 || !label.endsWith(")")) return;
  const layouts = index.products[label.slice(0, start)];
  if (!layouts) return;
  const tokens = label.slice(start + 2, -1).split(",").map((t) => t.trim());
  let asset: string | undefined;
  for (const [mask, rules] of Object.entries(layouts)) {
    const positions = mask.split(",").map(Number);
    if (!positions.every((i) => dimension.test(tokens[i] ?? ""))) continue;
    const key = tokens.filter((_, i) => !positions.includes(i)).join("|");
    const match = rules[key];
    if (match) {
      if (asset && match !== asset) return;
      asset = match;
    }
  }
  return asset;
}

// The catalogue index is fetched once, when a product list is first displayed.
const PhotoWithIndex = lazy(async () => {
  const { default: index }: { default: PhotoIndex } = await import("../data/catalogPhotos.json")
    .catch(() => ({ default: { products: {}, suits: {} } }));
  const jacketPhotos = new Set(Object.values(index.suits));
  const suitOptions = new Map<string, { asset: string; color: string; pattern: string }[]>();
  for (const [key, asset] of Object.entries(index.suits)) {
    const [line, variation, color, pattern, fit] = key.split("|");
    const family = [line, variation, fit].join("|");
    const options = suitOptions.get(family) ?? [];
    options.push({ asset, color, pattern });
    suitOptions.set(family, options);
  }
  return { default: function IndexedPhoto({ name, suit, groupNames }: PhotoProps) {
    if (suit?.mixed) {
      const options = suitOptions.get([suit.line, suit.variation, suit.fit].map(normalize).join("|"));
      if (!options?.length) return null;
      return <div className="mx-4 mb-2 flex flex-wrap gap-3" aria-label="Фотографии расцветок">
        {options.map(({ asset, color, pattern }) =>
          <div key={[asset, color, pattern].join("|")} className="flex items-center gap-2 text-xs text-mute-soft">
            <PhotoButton asset={asset} name={[suit.variation, color, pattern].filter(Boolean).join(" · ")} jacket />
            <span>{[color, pattern].filter(Boolean).join(" · ")}</span>
          </div>
        )}
      </div>;
    }
    const asset = suit
      ? index.suits[[suit.line, suit.variation, suit.color, suit.pattern, suit.fit].map(normalize).join("|")]
      : productAsset(index, name);
    if (!asset || groupNames?.some((item) => productAsset(index, item) !== asset)) return null;
    return <PhotoButton key={asset} name={name} asset={asset} jacket={jacketPhotos.has(asset)} />;
  } };
});

export function CatalogPhoto(props: PhotoProps) {
  return <Suspense fallback={null}><PhotoWithIndex {...props} /></Suspense>;
}

function PhotoButton({ name, asset, jacket }: { name: string; asset: string; jacket: boolean }) {
  const [pinned, setPinned] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null);
  const [missing, setMissing] = useState(false);
  const [previewFailed, setPreviewFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const dialog = useRef<HTMLDialogElement>(null);
  const prefix = "/catalog-photos/" + asset;
  const caption = jacket ? "На фото пиджак этой вариации" : "Фото вариации";

  function hideHover() {
    clearTimeout(timer.current);
    setAnchor(null);
  }

  function showHover(button: HTMLButtonElement) {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const rect = button.getBoundingClientRect();
      const width = Math.min(216, window.innerWidth - 16);
      const left = rect.right + width + 12 < window.innerWidth ? rect.right + 8 : rect.left - width - 8;
      setAnchor({
        left: Math.max(8, Math.min(left, window.innerWidth - width - 8)),
        top: Math.max(8, Math.min(rect.top - 30, window.innerHeight - 300)),
      });
    }, 180);
  }

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (pinned) dialog.current?.showModal();
  }, [pinned]);
  useEffect(() => {
    if (!anchor) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") hideHover(); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", hideHover, true);
    window.addEventListener("resize", hideHover);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", hideHover, true);
      window.removeEventListener("resize", hideHover);
    };
  }, [anchor]);

  if (missing) return null;
  return <>
    <button type="button" className="catalog-photo-thumb" aria-label={"Открыть фото вариации · " + name}
      aria-haspopup="dialog"
      onPointerEnter={(e) => { if (e.pointerType === "mouse" && !pinned) showHover(e.currentTarget); }}
      onPointerLeave={hideHover} onBlur={hideHover}
      onClick={(e) => { e.stopPropagation(); hideHover(); setPreviewFailed(false); setPinned(true); }}>
      <img src={prefix + "-s.webp"} width="40" height="48" loading="lazy" decoding="async" alt=""
        onError={() => setMissing(true)} />
    </button>
    {anchor && !pinned && createPortal(<div className="catalog-photo-hover" style={anchor} aria-hidden="true">
      <img src={prefix + "-l.webp"} alt="" onError={hideHover} />
    </div>, document.body)}
    {pinned && createPortal(<dialog ref={dialog} className="catalog-photo-dialog" aria-label={"Фото · " + name}
      onClose={() => setPinned(false)}
      onClick={(e) => { e.stopPropagation(); if (e.target === e.currentTarget) e.currentTarget.close(); }}>
      <figure>
        <button type="button" autoFocus aria-label="Закрыть фото" onClick={() => dialog.current?.close()}>×</button>
        {previewFailed ? <p role="status">Увеличенное фото не загрузилось</p> :
          <img src={prefix + "-l.webp"} alt={caption + " · " + name} onError={() => setPreviewFailed(true)} />}
        <figcaption><span>{caption}</span>{name}</figcaption>
      </figure>
    </dialog>, document.body)}
  </>;
}
