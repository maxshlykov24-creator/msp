import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Check, Package, ScanLine } from "lucide-react";
import { TASK_QUEUES, taskActionLabel, taskKindLabel, type Product } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";
import { Button } from "./ui";
import { assigneeLabel, createdByLabel } from "./CreateTaskForm";
import { BarcodeScannerModal } from "./BarcodeScanner";
import { money, shortDate, timeOf } from "../lib/format";
import { useStore } from "../store";

export interface TaskDetailModel {
  id: string;
  kind: string;
  title: string;
  store?: string;
  assigneeRole?: string;
  dealNumber?: number;
  status: "pending" | "done" | string;
  createdAt: string;
  createdBy?: string;
  metadata?: Record<string, unknown> | null;
}

export interface TaskPositionLine {
  name: string;
  quantity?: number;
  productId?: string;
  barcode?: string;
  code?: string;
  article?: string;
}

function isReadableProductName(name: string | undefined | null): boolean {
  const s = (name ?? "").trim();
  if (!s) return false;
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) return false;
  if (s.startsWith("name:")) return false;
  return true;
}

/** Достаём позиции из metadata (объекты с name или старые строки). */
export function positionsFromTaskMeta(
  meta?: Record<string, unknown> | null
): TaskPositionLine[] {
  const raw = meta?.positions;
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const out: TaskPositionLine[] = [];
  for (let i = 0; i < raw.length; i++) {
    const row = raw[i];
    if (typeof row === "string") {
      const name = row.trim();
      if (name) out.push({ name: isReadableProductName(name) ? name : `Позиция ${i + 1}` });
      continue;
    }
    if (!row || typeof row !== "object") continue;
    const o = row as {
      name?: unknown;
      productId?: unknown;
      quantity?: unknown;
      barcode?: unknown;
      code?: unknown;
      article?: unknown;
    };
    const productId = typeof o.productId === "string" ? o.productId : undefined;
    const rawName = typeof o.name === "string" ? o.name.trim() : "";
    const name = isReadableProductName(rawName)
      ? rawName
      : `Позиция ${i + 1}`;
    const quantity = Number(o.quantity);
    out.push({
      name,
      quantity: Number.isFinite(quantity) && quantity > 0 ? quantity : undefined,
      productId,
      barcode: typeof o.barcode === "string" ? o.barcode : undefined,
      code: typeof o.code === "string" ? o.code : undefined,
      article: typeof o.article === "string" ? o.article : undefined,
    });
  }
  return out;
}

export function routeFromTaskMeta(meta?: Record<string, unknown> | null): {
  from: string;
  to: string;
} | null {
  const from = typeof meta?.from === "string" ? meta.from.trim() : "";
  const to = typeof meta?.to === "string" ? meta.to.trim() : "";
  if (!from && !to) return null;
  return { from: from || "—", to: to || "—" };
}

const ROLE_SHORT: Record<string, string> = Object.fromEntries(
  TASK_QUEUES.map((q) => [q.role, q.short])
);

function dealKindFromMeta(meta?: Record<string, unknown> | null): string | null {
  if (typeof meta?.dealKindLabel === "string" && meta.dealKindLabel.trim()) {
    return meta.dealKindLabel.trim();
  }
  if (meta?.dealKind === "deferred") return "Отложка";
  if (meta?.dealKind === "promise") return "Обещание";
  if (meta?.dealKind === "delivery") return "Доставка";
  return null;
}

/** Кто поставил / ответственный / отправил — без лишнего шума. */
function peopleLines(task: TaskDetailModel): string[] {
  const meta = task.metadata ?? {};
  const roleKey =
    (typeof meta.sentByRole === "string" && meta.sentByRole) ||
    (typeof meta.authorRole === "string" && meta.authorRole) ||
    task.assigneeRole ||
    "";
  const role = ROLE_SHORT[roleKey] ?? roleKey;
  const lines: string[] = [];

  if (task.kind === "movement_accept") {
    const sent =
      (typeof meta.sentBy === "string" && meta.sentBy.trim()) || task.createdBy?.trim() || "";
    if (sent) lines.push(`от ${role} (${sent})`);
    else if (role) lines.push(`от ${role}`);
    return lines;
  }

  if (task.kind === "movement") {
    const author = task.createdBy?.trim() || "";
    const responsible =
      typeof meta.assigneeName === "string" ? meta.assigneeName.trim() : "";
    if (author && responsible && author !== responsible) {
      lines.push(`поставил ${author}`);
      lines.push(`ответственный ${responsible}`);
    } else if (author) {
      lines.push(`поставил ${author}${role ? ` · ${role}` : ""}`);
    } else if (responsible) {
      lines.push(`ответственный ${responsible}`);
    }
    return lines;
  }
  return [];
}

function codesOf(p: TaskPositionLine): string[] {
  return [p.barcode, p.code, p.article]
    .map((v) => (v ?? "").trim().toLowerCase())
    .filter(Boolean);
}

/**
 * Карточка задачи: маршрут откуда→куда и список позиций.
 * Перемещение / приёмка — обязательный скан позиций перед «Товар в пути» / «Товар отложен».
 */
export function TaskDetail({
  task,
  onClose,
  onComplete,
  onOpenDeal,
}: {
  task: TaskDetailModel;
  onClose: () => void;
  onComplete?: () => void;
  onOpenDeal?: (dealNumber: number) => void;
}) {
  const { deals } = useStore();
  const meta = task.metadata ?? undefined;
  const route = routeFromTaskMeta(meta);
  const basePositions = useMemo(() => {
    const list = positionsFromTaskMeta(meta);
    if (!task.dealNumber || list.length === 0) return list;
    const deal = deals.find((d) => d.number === task.dealNumber);
    if (!deal) return list;
    return list.map((p) => {
      if (isReadableProductName(p.name) && !p.name.startsWith("Позиция ")) return p;
      const item = p.productId
        ? deal.items.find((it) => it.productId === p.productId)
        : undefined;
      return item && isReadableProductName(item.name)
        ? { ...p, name: item.name, barcode: p.barcode || item.barcode }
        : p;
    });
  }, [meta, task.dealNumber, deals]);
  const [resolvedNames, setResolvedNames] = useState<Record<string, string>>({});
  const [scannedKeys, setScannedKeys] = useState<Set<string>>(() => new Set());
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanOk, setScanOk] = useState<string | null>(null);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [manualCode, setManualCode] = useState("");
  const scanInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (USE_MOCK) return;
    const need = basePositions.filter(
      (p) => p.productId && (!isReadableProductName(p.name) || p.name.startsWith("Позиция "))
    );
    if (need.length === 0) return;
    let cancelled = false;
    void (async () => {
      const entries: Record<string, string> = {};
      await Promise.all(
        need.map(async (p) => {
          try {
            const data = await api.get<{ name?: string }>(
              `/catalog/${encodeURIComponent(p.productId!)}/stock?cache=1`
            );
            if (data?.name && isReadableProductName(data.name)) {
              entries[p.productId!] = data.name;
            }
          } catch {
            // каталог недоступен — оставим «Позиция N»
          }
        })
      );
      if (!cancelled && Object.keys(entries).length) {
        setResolvedNames((prev) => ({ ...prev, ...entries }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [basePositions, task.id]);

  const positions = useMemo(
    () =>
      basePositions.map((p) =>
        p.productId && resolvedNames[p.productId]
          ? { ...p, name: resolvedNames[p.productId]! }
          : p
      ),
    [basePositions, resolvedNames]
  );

  const needsSetup = task.kind === "movement" && meta?.needsSetup === true;
  const isMove = task.kind === "movement" || task.kind === "movement_accept";
  const isReserveOps =
    task.kind === "reserve" || task.kind === "reserve_call" || task.kind === "unreserve";
  const isAssemble = task.kind === "assemble_cdek";
  const isTakeToCdek = task.kind === "take_to_cdek";
  const isPickupFromCdek = task.kind === "pickup_from_cdek";
  const isSary = task.kind === "sary_send";
  const isDeliveryCompact =
    isAssemble || isTakeToCdek || isPickupFromCdek || task.kind === "call_courier";
  /** Позиции списком: сборка / отнести / забрать. Скан обязателен только на сборке. */
  const showDeliveryPositions = isAssemble || isTakeToCdek || isPickupFromCdek;
  const saryPhone = typeof meta?.phone === "string" ? meta.phone.trim() : "";
  const saryFriend =
    typeof meta?.client === "string" && meta.client.trim() ? meta.client.trim() : "";
  const saryBuyer =
    typeof meta?.buyer === "string" && meta.buyer.trim() ? meta.buyer.trim() : "";
  const saryAmount =
    typeof meta?.amount === "number" && meta.amount > 0 ? meta.amount : 1000;
  const who = assigneeLabel(task);
  const creator = createdByLabel(task);
  const dealKind = dealKindFromMeta(meta);
  const people = peopleLines(task);
  const requiresScan =
    (isMove || isAssemble) &&
    task.status === "pending" &&
    !needsSetup &&
    positions.length > 0;
  const clientName =
    typeof meta?.client === "string" && meta.client.trim() ? meta.client.trim() : "";
  const untilRaw = typeof meta?.reservedUntil === "string" ? meta.reservedUntil : "";
  const untilLabel = (() => {
    const m = untilRaw.trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? `${m[3]}.${m[2]}.${m[1]}` : untilRaw.trim() || null;
  })();
  const positionNames = positions.map((p) => p.name?.trim()).filter(Boolean) as string[];
  const compactHeadline = isSary
    ? saryFriend || saryPhone || task.title
    : clientName ||
      (positionNames.length > 0 ? positionNames.join(" · ") : task.title);

  const positionKey = (p: TaskPositionLine, i: number) =>
    p.productId ? `id:${p.productId}` : `name:${p.name}:${i}`;

  const allScanned =
    !requiresScan ||
    positions.every((p, i) => scannedKeys.has(positionKey(p, i)));

  async function applyScan(raw: string) {
    const code = raw.trim();
    if (!code) return;
    setScanError(null);
    setScanOk(null);
    const normalized = code.toLowerCase();

    let matchIndex = positions.findIndex((p) => codesOf(p).includes(normalized));

    if (matchIndex < 0 && !USE_MOCK) {
      try {
        const hits = await api.getQuery<Product[]>("/catalog/search", {
          q: code,
          limit: 8,
        });
        const ids = new Set(hits.map((h) => h.id));
        matchIndex = positions.findIndex((p) => p.productId && ids.has(p.productId));
      } catch {
        // поиск недоступен — остаёмся на локальных кодах
      }
    }

    if (matchIndex < 0) {
      setScanError(`Штрихкод «${code}» не из этой задачи`);
      return;
    }
    const key = positionKey(positions[matchIndex]!, matchIndex);
    if (scannedKeys.has(key)) {
      setScanOk(`Уже отсканировано: ${positions[matchIndex]!.name}`);
      return;
    }
    setScannedKeys((prev) => new Set(prev).add(key));
    setScanOk(`Сошлось: ${positions[matchIndex]!.name}`);
    setManualCode("");
  }

  useEffect(() => {
    if (!requiresScan) return;
    const t = window.setTimeout(() => scanInputRef.current?.focus(), 120);
    return () => window.clearTimeout(t);
  }, [requiresScan, task.id]);

  return (
    <div className="space-y-5">
      {scannerOpen && (
        <BarcodeScannerModal
          onDetected={(code) => {
            setScannerOpen(false);
            void applyScan(code);
          }}
          onClose={() => setScannerOpen(false)}
        />
      )}

      <div>
        <div className="text-[12px] text-mute uppercase tracking-wide mb-1">
          {taskKindLabel(task.kind)}
          {task.status === "done" ? " · выполнено" : ""}
        </div>
        <h2 className="text-xl font-bold text-white leading-snug">
          {isMove
            ? positionNames.length > 0
              ? positionNames.join(" · ")
              : task.title
            : isReserveOps || isDeliveryCompact || isSary
              ? compactHeadline
              : task.title}
        </h2>
        {isMove &&
          (() => {
            const line = [
              route ? `${route.from} → ${route.to}` : null,
              dealKind,
              ...people,
              `${shortDate(task.createdAt)} ${timeOf(task.createdAt)}`,
            ]
              .filter(Boolean)
              .join(" · ");
            return line ? (
              <div className="mt-1.5 text-[13px] text-mute">{line}</div>
            ) : null;
          })()}
        {isReserveOps && (
          <div className="mt-1.5 text-[13px] text-mute">
            {[
              task.store || null,
              typeof meta?.consultant === "string" && meta.consultant.trim()
                ? meta.consultant.trim()
                : null,
              untilLabel
                ? task.kind === "reserve"
                  ? `до ${untilLabel}`
                  : `срок ${untilLabel}`
                : null,
              dealKind || "Отложка",
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
        )}
        {isDeliveryCompact && (
          <div className="mt-1.5 text-[13px] text-mute">
            {[task.store || null, dealKind || "Доставка"].filter(Boolean).join(" · ")}
          </div>
        )}
        {isSary && (
          <div className="mt-1.5 text-[13px] text-mute">
            {[
              money(saryAmount),
              "Сарафан",
              saryFriend && saryPhone ? saryPhone : null,
              saryBuyer ? `клиент ${saryBuyer}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
        )}
        {!isMove && !isReserveOps && !isDeliveryCompact && !isSary && (
          <div className="mt-1.5 text-[13px] text-mute flex flex-wrap gap-x-3 gap-y-0.5">
            {task.store && <span>{task.store}</span>}
            <span>{who}</span>
            <span>
              {shortDate(task.createdAt)} {timeOf(task.createdAt)}
            </span>
            {creator && <span>поставил: {creator}</span>}
          </div>
        )}
      </div>

      {isMove && route && (
        <div className="rounded-xl border border-ink-700 bg-ink-900/50 p-4">
          <div className="grid grid-cols-[1fr_auto_1fr] gap-3 items-stretch">
            <div className="min-w-0 rounded-lg bg-ink-800/80 px-3 py-3">
              <div className="text-[11px] uppercase tracking-wide text-mute mb-1">Откуда</div>
              <div className="text-[15px] font-semibold text-white break-words leading-snug">
                {route.from}
              </div>
            </div>
            <div className="flex items-center justify-center text-gold shrink-0 px-0.5">
              <ArrowRight size={22} strokeWidth={2.25} />
            </div>
            <div className="min-w-0 rounded-lg bg-ink-800/80 px-3 py-3">
              <div className="text-[11px] uppercase tracking-wide text-mute mb-1">Куда</div>
              <div className="text-[15px] font-semibold text-white break-words leading-snug">
                {route.to}
              </div>
            </div>
          </div>
        </div>
      )}

      {(isMove || showDeliveryPositions) && (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Package size={15} className="text-gold" />
            <div className="text-[13px] font-semibold text-white">
              Позиции
              {positions.length > 0 ? ` · ${positions.length}` : ""}
              {requiresScan && (
                <span className="text-mute font-normal">
                  {" "}
                  · отсканировано {scannedKeys.size}/{positions.length}
                </span>
              )}
            </div>
          </div>
          {needsSetup && positions.length === 0 ? (
            <div className="rounded-lg border border-amber-400/35 bg-amber-400/10 px-3 py-3 text-[13px] text-amber-100/95">
              Позиции ещё не выбраны. Откройте заявку и оформите перемещение: откуда, куда и что
              везём.
            </div>
          ) : positions.length === 0 ? (
            <div className="rounded-lg border border-ink-700 px-3 py-3 text-[13px] text-mute">
              Список позиций не сохранён в задаче. Откройте заявку — там полный состав.
            </div>
          ) : (
            <ul className="rounded-lg border border-ink-700 divide-y divide-ink-700 overflow-hidden">
              {positions.map((p, i) => {
                const key = positionKey(p, i);
                const ok = scannedKeys.has(key);
                return (
                  <li
                    key={`${p.productId ?? p.name}-${i}`}
                    className={`flex items-start gap-3 px-3 py-2.5 ${
                      ok ? "bg-emerald-500/10" : "bg-ink-900/40"
                    }`}
                  >
                    <span className="text-mute text-[12px] tabular-nums w-5 shrink-0 pt-0.5">
                      {i + 1}.
                    </span>
                    <span className="flex-1 min-w-0 text-[14px] text-white break-words">
                      {p.name}
                      {requiresScan && (
                        <span className={`block text-[12px] mt-0.5 ${ok ? "text-emerald-300" : "text-mute"}`}>
                          {ok ? "Отсканировано" : "Нужен скан"}
                        </span>
                      )}
                    </span>
                    {p.quantity != null && (
                      <span className="shrink-0 text-[13px] font-semibold text-gold-soft tabular-nums">
                        × {p.quantity}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {requiresScan && (
            <div className="mt-3 space-y-2">
              <div className="field-label">Сканирование для перепроверки</div>
              <div className="flex flex-wrap gap-2">
                <input
                  ref={scanInputRef}
                  className="input flex-1 min-w-[12rem]"
                  value={manualCode}
                  onChange={(e) => setManualCode(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void applyScan(manualCode);
                    }
                  }}
                  placeholder="Штрихкод или артикул…"
                  autoComplete="off"
                  inputMode="none"
                />
                <Button variant="subtle" onClick={() => setScannerOpen(true)}>
                  <ScanLine size={15} /> Камера
                </Button>
                <Button
                  variant="subtle"
                  disabled={!manualCode.trim()}
                  onClick={() => void applyScan(manualCode)}
                >
                  Проверить
                </Button>
              </div>
              {scanError && <div className="text-[12px] text-amber-300/90">{scanError}</div>}
              {scanOk && <div className="text-[12px] text-emerald-300/90">{scanOk}</div>}
              {!allScanned && (
                <div className="text-[12px] text-mute">
                  Отсканируйте все позиции — только после этого станет доступна кнопка «
                  {taskActionLabel(task.kind)}».
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {!isMove && !showDeliveryPositions && positions.length > 0 && (
        <ul className="rounded-lg border border-ink-700 divide-y divide-ink-700 overflow-hidden">
          {positions.map((p, i) => (
            <li key={i} className="px-3 py-2.5 text-[14px] text-white bg-ink-900/40">
              {p.name}
              {p.quantity != null ? ` × ${p.quantity}` : ""}
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2 pt-1 border-t border-ink-700">
        <Button variant="subtle" onClick={onClose}>
          Закрыть
        </Button>
        {task.dealNumber != null && onOpenDeal && (
          <Button variant="subtle" onClick={() => onOpenDeal(task.dealNumber!)}>
            Заявка #{task.dealNumber}
          </Button>
        )}
        {task.status === "pending" && !needsSetup && onComplete && (
          <Button
            disabled={requiresScan && !allScanned}
            onClick={() => {
              if (requiresScan && !allScanned) return;
              onComplete();
              onClose();
            }}
          >
            <Check size={15} /> {taskActionLabel(task.kind)}
          </Button>
        )}
      </div>
    </div>
  );
}
