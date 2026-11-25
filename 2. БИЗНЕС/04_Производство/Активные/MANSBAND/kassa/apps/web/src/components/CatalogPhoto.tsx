import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Images } from "lucide-react";
import type { SuitModel } from "@kassa/shared";
import {
  catalogThumbMode,
  prepareCatalogPhotos,
  resolveCatalogPhoto,
  type PhotoChoice,
  type PhotoIndex,
} from "../lib/catalogPhotoMatch";

type PhotoProps = { name: string; suit?: SuitModel; groupNames?: string[] };

// The catalogue index is fetched once, when a product list is first displayed.
const PhotoWithIndex = lazy(async () => {
  const { default: index }: { default: PhotoIndex } = await import("../data/catalogPhotos.json")
    .catch(() => ({ default: { products: {}, suits: {} } }));
  const prepared = prepareCatalogPhotos(index);
  return { default: function IndexedPhoto({ name, suit, groupNames }: PhotoProps) {
    const photo = resolveCatalogPhoto(prepared, { name, suit, groupNames });
    if (!photo) return null;
    return <PhotoButton key={suit?.mixed ? suit.modelId : photo.asset}
      name={suit?.mixed ? suit.variation : name} asset={photo.asset} jacket={photo.jacket}
      choices={photo.choices} differentAppearance={photo.differentAppearance} />;
  } };
});

export function CatalogPhoto(props: PhotoProps) {
  return <Suspense fallback={null}><PhotoWithIndex {...props} /></Suspense>;
}

function PhotoButton({ name, asset, jacket, choices, differentAppearance = false }: {
  name: string;
  asset: string;
  jacket: boolean;
  choices?: PhotoChoice[];
  differentAppearance?: boolean;
}) {
  const [pinned, setPinned] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null);
  const [useLargeThumb, setUseLargeThumb] = useState(false);
  const [thumbFailed, setThumbFailed] = useState(false);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [selected, setSelected] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const dialog = useRef<HTMLDialogElement>(null);
  const prefix = "/catalog-photos/" + asset;
  const caption = jacket ? "На фото пиджак этой вариации" : "Фото вариации";
  const current = choices?.[selected];
  const photoName = [name, current?.name].filter(Boolean).join(" · ");
  const action = differentAppearance ? "Открыть фото других расцветок" : "Открыть фото вариации";
  const buttonName = [action, name, choices?.[0]?.name].filter(Boolean).join(" · ");

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

  const mode = catalogThumbMode(thumbFailed, choices?.length ?? 0);
  if (mode === "hidden") return null;
  const thumb = prefix + (useLargeThumb ? "-l.webp" : "-s.webp");
  return <>
    <button type="button" className={`catalog-photo-thumb${mode === "icon" ? " catalog-photo-icon" : ""}`}
      aria-label={buttonName} title={differentAppearance ? buttonName : undefined}
      aria-haspopup="dialog"
      onPointerEnter={(e) => { if (e.pointerType === "mouse" && !pinned && mode === "image") showHover(e.currentTarget); }}
      onPointerLeave={hideHover} onBlur={hideHover}
      onClick={(e) => { e.stopPropagation(); hideHover(); setSelected(0); setPreviewFailed(false); setPinned(true); }}>
      {mode === "icon" ? <Images size={19} aria-hidden /> :
        <img src={thumb} width="40" height="48" loading="lazy" decoding="async" alt=""
          onError={(event) => {
            const url = event.currentTarget.currentSrc || event.currentTarget.src;
            if (url.endsWith("-s.webp")) setUseLargeThumb(true);
            else setThumbFailed(true);
          }} />}
      {choices && choices.length > 1 && <span className="catalog-photo-count" aria-hidden>{choices.length}</span>}
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
          <img src={"/catalog-photos/" + (current?.asset ?? asset) + "-l.webp"}
            alt={caption + " · " + photoName} onError={() => setPreviewFailed(true)} />}
        <figcaption><span>{caption}</span>{photoName}</figcaption>
        {choices && choices.length > 1 && (
          <div className="flex flex-wrap gap-2 px-4 pb-4" aria-label="Расцветки на фото">
            {choices.map((choice, i) => (
              <button key={choice.name + choice.asset} type="button" aria-pressed={selected === i}
                className={`rounded-md border px-2 py-1 text-xs ${selected === i ? "border-gold text-white" : "border-ink-600 text-mute-soft"}`}
                onClick={() => { setSelected(i); setPreviewFailed(false); }}>
                {choice.name || `Фото ${i + 1}`}
              </button>
            ))}
          </div>
        )}
      </figure>
    </dialog>, document.body)}
  </>;
}
