import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { PhotoReviewComment, PhotoReviewData, PhotoReviewImage } from "@kassa/shared";
import { ArrowLeft, Camera, MessageSquare, Search } from "lucide-react";
import { api } from "../api/client";
import { Modal } from "../components/ui";

const endpoint = "/catalog/photo-review";
const pageSize = 24;
const formatDate = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

function normalize(text: string) {
  return text.normalize("NFC").toLowerCase().replace(/ё/g, "е").replace(/\//g, "-");
}

function Frame({ photo, onOpen }: { photo: PhotoReviewImage; onOpen: (photo: PhotoReviewImage) => void }) {
  return (
    <button type="button" onClick={() => onOpen(photo)} className="w-full min-w-0 text-left group"
      aria-label={`Открыть фото ${photo.name}`}>
      <img src={photo.url + "-s.webp"} alt="" loading="lazy" width={144} height={144}
        className="w-full h-40 bg-ink-950 rounded-lg object-contain border border-ink-700 group-hover:border-gold" />
      <span className="block mt-1 break-all text-xs text-mute-soft group-hover:text-white">{photo.name}</span>
    </button>
  );
}

function Comments({ target, comments, onPosted }: {
  target: string;
  comments: PhotoReviewComment[];
  onPosted: (comment: PhotoReviewComment) => void;
}) {
  const [text, setText] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<{ id: string; text: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const entered = text.trim();
    if (!entered || posting) return;
    if (pending.current?.text !== entered) pending.current = { id: crypto.randomUUID(), text: entered };
    setPosting(true);
    setError("");
    try {
      const saved = await api.post<PhotoReviewComment>(endpoint + "/comments", { ...pending.current, target });
      onPosted(saved);
      pending.current = null;
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить комментарий");
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="mt-3 border-t border-ink-700 pt-3">
      {comments.length > 0 && (
        <ul className="space-y-2 mb-3" aria-label="Сохранённые комментарии">
          {comments.map(comment => (
            <li key={comment.id} className="rounded-lg bg-ink-950 px-3 py-2">
              <div className="text-xs text-mute-soft">{comment.author} · {formatDate.format(new Date(comment.at))}</div>
              <div className="whitespace-pre-wrap break-words text-sm text-white">{comment.text}</div>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="flex flex-col items-start gap-2">
        <textarea aria-label="Комментарий" value={text} onChange={event => setText(event.target.value)}
          placeholder="Добавить комментарий" className="input w-full resize-y" rows={2} maxLength={1600} disabled={posting} />
        <button type="submit" disabled={!text.trim() || posting}
          className="inline-flex items-center gap-2 rounded-lg bg-ink-800 border border-ink-700 hover:border-gold px-3 py-1.5 text-sm text-gold-soft disabled:opacity-50">
          <MessageSquare size={14} aria-hidden />{posting ? "Сохраняю…" : "Сохранить комментарий"}
        </button>
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
      </form>
    </div>
  );
}

export default function PhotoReview() {
  const [data, setData] = useState<PhotoReviewData | null>(null);
  const [error, setError] = useState("");
  const [request, retry] = useState(0);
  const [mode, setMode] = useState<"cases" | "photos">("cases");
  const [query, setQuery] = useState("");
  const [show, setShow] = useState(pageSize);
  const [withoutComment, setWithoutComment] = useState(false);
  const [opened, setOpened] = useState<PhotoReviewImage | null>(null);

  useEffect(() => {
    let current = true;
    api.get<PhotoReviewData>(endpoint)
      .then(result => { if (current) setData(result); })
      .catch(err => { if (current) setError(err instanceof Error ? err.message : "Не удалось загрузить список"); });
    return () => { current = false; };
  }, [request]);

  const byTarget = useMemo(() => {
    const byTarget = new Map<string, PhotoReviewComment[]>();
    for (const comment of data?.comments ?? []) {
      const items = byTarget.get(comment.target) ?? [];
      items.push(comment);
      byTarget.set(comment.target, items);
    }
    return byTarget;
  }, [data]);
  function addComment(comment: PhotoReviewComment) {
    setData(previous => {
      if (!previous || previous.comments.some(item => item.id === comment.id && item.target === comment.target)) return previous;
      return { ...previous, comments: [...previous.comments, comment] };
    });
  }
  const needle = normalize(query.trim());
  const includes = (fields: string[]) => normalize(fields.join(" ")).includes(needle);
  const caseRows = (data?.cases ?? []).filter(row =>
    (!withoutComment || !byTarget.has(`case:${row.id}`)) &&
    includes([row.cell, row.code, row.category, row.reason, ...row.variants.flat(),
      ...row.photoIds.map(id => data?.photos[id]?.name ?? "")]));
  const imageRows = (data?.unpublished ?? []).filter(row =>
    (!withoutComment || !byTarget.has(`photo:${row.id}`)) &&
    includes([data?.photos[row.id]?.name ?? "", ...row.cells]));
  const count = mode === "cases" ? caseRows.length : imageRows.length;
  const tabs = [
    ["cases", "Спорные позиции", data?.cases.length],
    ["photos", "Фото без привязки", data?.unpublished.length],
  ] as const;

  return (
    <main className="max-w-6xl mx-auto">
      <a href="#products" className="inline-flex items-center gap-2 text-sm text-mute-soft hover:text-white">
        <ArrowLeft size={15} aria-hidden /> Товар
      </a>
      <h1 className="flex items-center gap-2 my-3 text-2xl font-extrabold text-white">
        <Camera className="text-gold" size={22} aria-hidden /> Проверка фото
      </h1>
      {error && (
        <div role="alert" className="card p-4 text-red-400">
          {error}
          <button type="button" className="ml-3 underline text-mute-soft" onClick={() => { setError(""); retry(n => n + 1); }}>
            Повторить
          </button>
        </div>
      )}
      {!data && !error && <p role="status" className="text-mute">Загрузка фотографий…</p>}
      {data && <>
        <div className="flex flex-wrap gap-2 mb-3">
          {tabs.map(([id, label, total]) => (
            <button key={id} type="button" aria-pressed={mode === id}
              onClick={() => { setMode(id); setShow(pageSize); setQuery(""); }}
              className={`rounded-lg border px-3 py-2 text-sm ${mode === id ? "border-gold text-gold-soft bg-ink-800" : "border-ink-700 text-mute-soft"}`}>
              {label} <span className="tabular-nums ml-1">{total}</span>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <label className="relative flex-1 min-w-[240px]">
            <Search className="absolute left-3 top-3 text-mute" size={16} aria-hidden />
            <input aria-label="Поиск по списку" placeholder="Артикул, ячейка или имя файла"
              className="input pl-9" value={query} onChange={event => { setQuery(event.target.value); setShow(pageSize); }} />
          </label>
          <label className="flex items-center gap-2 text-sm text-mute-soft">
            <input type="checkbox" checked={withoutComment} onChange={event => { setWithoutComment(event.target.checked); setShow(pageSize); }} />
            Без комментария
          </label>
        </div>
        {!count && <p className="card p-5 text-mute">Совпадений нет</p>}
        {mode === "cases" ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {caseRows.slice(0, show).map(row => {
              const target = `case:${row.id}`;
              return (
                <article key={row.id} className="card p-3 sm:p-4">
                  <header className="mb-2">
                    <h2 className="font-bold text-white break-words">{row.code}</h2>
                    <p className="text-sm text-mute-soft">{row.cell} · {row.category}</p>
                  </header>
                  <p className="text-sm text-gold-soft mb-3">{row.reason}</p>
                  {row.photoIds.length ? (
                    <div className="grid grid-cols-2 gap-2">
                      {row.photoIds.map(id => data.photos[id] && <Frame key={id} photo={data.photos[id]} onOpen={setOpened} />)}
                    </div>
                  ) : <div className="rounded-lg bg-ink-950 p-4 text-sm text-mute">Исходный кадр отсутствует</div>}
                  {row.variants.length > 0 && (
                    <div className="mt-3 text-sm text-mute-soft">
                      <p className="font-semibold text-white">Варианты в каталоге</p>
                      <ul className="mt-1 space-y-1 list-disc ml-5">
                        {row.variants.slice(0, 3).map(fields => <li key={fields.join("|")}>{fields.filter(Boolean).join(" · ")}</li>)}
                      </ul>
                      {row.variants.length > 3 && (
                        <details className="mt-2">
                          <summary className="cursor-pointer">Ещё {row.variants.length - 3}</summary>
                          <ul className="mt-1 space-y-1 list-disc ml-5">
                            {row.variants.slice(3).map(fields => <li key={fields.join("|")}>{fields.filter(Boolean).join(" · ")}</li>)}
                          </ul>
                        </details>
                      )}
                    </div>
                  )}
                  <Comments target={target} comments={byTarget.get(target) ?? []} onPosted={addComment} />
                </article>
              );
            })}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {imageRows.slice(0, show).map(row => {
              const photo = data.photos[row.id];
              if (!photo) return null;
              const target = `photo:${row.id}`;
              return (
                <article key={row.id} className="card p-3 sm:p-4">
                  <Frame photo={photo} onOpen={setOpened} />
                  <p className="mt-2 text-sm text-mute-soft">
                    {row.extraAngle ? "Дополнительный ракурс" : row.cells.length ? `Ячейки ${row.cells.join(", ")}` : "Отдельный файл без строки в таблице"}
                  </p>
                  <Comments target={target} comments={byTarget.get(target) ?? []} onPosted={addComment} />
                </article>
              );
            })}
          </div>
        )}
        {count > show && (
          <button type="button" onClick={() => setShow(value => value + pageSize)}
            className="block mx-auto my-5 rounded-lg border border-ink-700 px-4 py-2 text-sm text-gold-soft hover:border-gold">
            Показать ещё · {show} из {count}
          </button>
        )}
      </>}
      {opened && (
        <Modal open onClose={() => setOpened(null)} title={opened.name}>
          <img src={opened.url + "-l.webp"} alt={opened.name} className="mx-auto max-w-full max-h-[72vh] object-contain rounded-lg" />
        </Modal>
      )}
    </main>
  );
}
