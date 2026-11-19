import { useEffect, useMemo, useState } from "react";
import { catalogSearchTerms, matchesCatalogTerms, type SuitFrontConflict } from "@kassa/shared";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";

export function SuitFrontReview() {
  const { user } = useAuth();
  return <section className="max-w-4xl mx-auto">
    <a href="#suits" className="text-sm text-mute-soft underline underline-offset-4">К костюмам</a>
    <h1 className="mt-3 text-2xl font-extrabold text-white">Проверка типа борта</h1>
    {user?.role === "admin"
      ? <ConflictList key={user.id} />
      : <p role="status" className="mt-3 text-mute-soft">Раздел доступен только администраторам.</p>}
  </section>;
}

function ConflictList() {
  const [models, setModels] = useState<SuitFrontConflict[] | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  useEffect(() => {
    let active = true;
    setError("");
    api.get<{ models: SuitFrontConflict[] }>("/catalog/suit-front-conflicts")
      .then(result => { if (active) setModels(result.models); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : "Не удалось загрузить модели"); });
    return () => { active = false; };
  }, [attempt]);
  const visible = useMemo(() => {
    const terms = catalogSearchTerms(query);
    if (!terms.length) return models ?? [];
    return (models ?? []).filter(m => m.jackets.some(j => matchesCatalogTerms(
      terms, [m.fit, m.height, j.name, j.size], [m.variation]
    )));
  }, [models, query]);

  return <>
    <p className="mt-1 mb-4 text-sm text-mute-soft">
      У этих моделей в названиях связанных пиджаков указаны оба типа борта. Раскрой вариацию, чтобы сверить карточки МойСклада по названию и размеру.
    </p>
    {error ? <div role="alert" className="card p-4">{error}
      <button type="button" className="ml-3 underline" onClick={() => setAttempt(n => n + 1)}>Повторить</button>
    </div> : models === null ? <p role="status">Загружаем модели…</p>
      : models.length === 0 ? <p role="status" className="card p-4">Сейчас расхождений нет.</p>
      : <>
        <input type="search" className="input mb-2" aria-label="Найти модель для проверки"
          placeholder="Вариация, название пиджака или размер" value={query} onChange={e => setQuery(e.target.value)} />
        <p role="status" className="mb-3 text-sm text-mute-soft">
          {query.trim() ? `Найдено ${visible.length} из ${models.length}` : `Моделей для проверки · ${models.length}`}
        </p>
        <div className="space-y-2">{visible.map(m => (
          <details key={m.modelId} className="card p-0 overflow-hidden">
            <summary className="cursor-pointer px-4 py-3 text-white">
              <span className="font-semibold break-all">{m.variation}</span>
              <span className="ml-2 text-sm text-mute-soft">
                {[m.fit, m.height && `ростовка ${m.height}`, m.line === "smoking" && "смокинг"].filter(Boolean).join(" · ")}
              </span>
            </summary>
            <ul className="divide-y divide-ink-800 border-t border-ink-700">
              {m.jackets.map(j => <li key={j.msId} className="px-4 py-2 grid gap-1 sm:grid-cols-[9rem_1fr] text-sm">
                <span className="text-mute-soft">{j.fronts.join(" и ") || "Тип не указан"}</span>
                <span className="min-w-0 break-words text-white">{j.name}
                  {j.size && <span className="block text-xs text-mute">Размер {j.size}</span>}
                </span>
              </li>)}
            </ul>
          </details>
        ))}</div>
      </>}
  </>;
}
