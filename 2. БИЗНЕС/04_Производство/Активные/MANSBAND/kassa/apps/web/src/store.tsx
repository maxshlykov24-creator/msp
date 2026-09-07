import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { mansbandPayoutAmount } from "@kassa/shared";
import type { Certificate, Deal, QueueItem, SaryPayout, Store } from "./data/types";
import { CERTIFICATES, DEALS, QUEUE, SARY } from "./data/mock";
import { api, USE_MOCK } from "./api/client";
import { connectWs } from "./api/ws";
import type { PhotoUploadItem } from "./lib/photo";

interface StoreState {
  deals: Deal[];
  dealsLoading: boolean;
  certificates: Certificate[];
  queue: QueueItem[];
  sary: SaryPayout[];
  activeStore: Store;
  activeConsultant: string;
  setActiveStore: (s: Store) => void;
  setActiveConsultant: (c: string) => void;
  addDeal: (d: Deal, photos?: PhotoUploadItem[]) => Promise<Deal>;
  updateDealStage: (id: string, stage: string, reason?: string) => void;
  /** Полное обновление заявки (карточка как форма). */
  updateDeal: (id: string, patch: Partial<Deal> & { reason?: string }) => Promise<Deal | null>;
  /** Положить в список заявку, которую уже вернул сервер. */
  replaceDeal: (deal: Deal) => void;
  addDealComment: (id: string, text: string, who: string) => void;
  /** Продажа компании: консультант отмечает выдачу товара и передачу документов. */
  handoverCompany: (id: string, patch: { issued?: boolean; documentsHanded?: boolean }) => void;
  addQueueItem: (q: QueueItem) => void;
  /** `amount` не задан — закрыть остаток целиком; задан — частичная выдача. */
  issueQueueItem: (id: string, methodId: string, amount?: number) => void;
  /** Вернуть закрытую позицию Эдвина/Миши обратно в работу. */
  reopenQueueItem: (id: string) => Promise<void>;
  /** Новая выплата тому, кто направил клиента. */
  addSary: (input: {
    client: string;
    phone: string;
    amount: number;
    reason: string;
    refDealNumber?: number;
  }) => void;
  markSarySent: (id: string) => void;
  /** Пачка за день: одна отметка, один скриншот и один способ перевода на группу. */
  markSaryBatchSent: (
    ids: string[],
    screenshot: { filename: string; contentBase64: string },
    methodId: string
  ) => Promise<void>;
  redeemCertificate: (number: string, amount: number) => void;
  nextNumber: () => number;
  findByPhone: (phone: string) => Deal | undefined;
  findSlivByPhone: (phone: string) => Deal | undefined;
}

const Ctx = createContext<StoreState | null>(null);

/** Консультант на форме (#new) — не трогаем фоновые списки, чтобы ничего не «слетело». */
function isDealFormOpen(): boolean {
  try {
    return window.location.hash.replace(/^#/, "").startsWith("new");
  } catch {
    return false;
  }
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [deals, setDeals] = useState<Deal[]>(USE_MOCK ? DEALS : []);
  const [dealsLoading, setDealsLoading] = useState(!USE_MOCK);
  const [certificates, setCertificates] = useState<Certificate[]>(USE_MOCK ? CERTIFICATES : []);
  const [queue, setQueue] = useState<QueueItem[]>(USE_MOCK ? QUEUE : []);
  const [sary, setSary] = useState<SaryPayout[]>(USE_MOCK ? SARY : []);
  const [activeStore, setActiveStore] = useState<Store>("На Бауманской");
  const [activeConsultant, setActiveConsultant] = useState<string>("Матвей");

  // Загрузка реальных данных + подписка на WebSocket (только не в мок-режиме).
  // Пока консультант на #new (продажа и др.) — не трогаем deals/certs/queue и глушим WS:
  // иначе на телефоне фоновый /deals или reconnect «съедает» соединения и риск сброса UI.
  useEffect(() => {
    if (USE_MOCK) return;
    let dealsTimer: ReturnType<typeof setTimeout> | null = null;
    let wsDelayTimer: ReturnType<typeof setTimeout> | null = null;
    let disconnectWs: (() => void) | null = null;
    let dealsInFlight = false;
    let wasOnForm = isDealFormOpen();

    const reloadDealsNow = (opts?: { initial?: boolean; force?: boolean }) => {
      if (!opts?.force && isDealFormOpen()) return;
      if (dealsInFlight) return;
      dealsInFlight = true;
      if (opts?.initial) setDealsLoading(true);
      api
        .get<Deal[]>("/deals")
        .then(setDeals)
        .catch(() => {})
        .finally(() => {
          dealsInFlight = false;
          setDealsLoading(false);
        });
    };
    const reloadDeals = () => {
      if (dealsTimer) clearTimeout(dealsTimer);
      dealsTimer = setTimeout(() => reloadDealsNow(), 2500);
    };
    const reloadCerts = (force = false) => {
      if (!force && isDealFormOpen()) return;
      api.get<Certificate[]>("/certificates").then(setCertificates).catch(() => {});
    };
    const reloadQueue = (force = false) => {
      if (!force && isDealFormOpen()) return;
      api.get<QueueItem[]>("/queue").then(setQueue).catch(() => {});
    };
    const reloadSary = () => api.get<SaryPayout[]>("/sary").then(setSary).catch(() => {});

    const stopWs = () => {
      if (wsDelayTimer) {
        clearTimeout(wsDelayTimer);
        wsDelayTimer = null;
      }
      disconnectWs?.();
      disconnectWs = null;
    };

    const startWs = () => {
      if (disconnectWs || isDealFormOpen()) return;
      // Задержка: на iOS лимит параллельных соединений; сначала отдаём HTTP
      wsDelayTimer = setTimeout(() => {
        wsDelayTimer = null;
        if (isDealFormOpen() || disconnectWs) return;
        disconnectWs = connectWs((ev) => {
          if (isDealFormOpen()) return;
          switch (ev.type) {
            case "deal.created":
            case "deal.updated":
            case "deal.stage_changed":
              reloadDeals();
              break;
            case "certificate.updated":
              reloadCerts();
              break;
            case "queue.updated":
              reloadQueue();
              break;
            case "sary.updated":
              reloadSary();
              break;
            default:
              break;
          }
        });
      }, 2000);
    };

    // Старт приложения: один раз грузим, даже если сразу открыли #new
    reloadDealsNow({ initial: true, force: true });
    reloadCerts(true);
    reloadQueue(true);
    reloadSary();
    if (!wasOnForm) startWs();

    const poll = setInterval(() => reloadDealsNow(), 60_000);

    const onHash = () => {
      const onForm = isDealFormOpen();
      if (onForm && !wasOnForm) {
        // Зашли на форму продажи — стопаем фоновые обновления
        stopWs();
        if (dealsTimer) {
          clearTimeout(dealsTimer);
          dealsTimer = null;
        }
      } else if (!onForm && wasOnForm) {
        // Вышли с формы — один тихий рефреш + снова WS
        reloadDealsNow({ force: true });
        reloadCerts(true);
        reloadQueue(true);
        startWs();
      }
      wasOnForm = onForm;
    };
    window.addEventListener("hashchange", onHash);

    return () => {
      stopWs();
      clearInterval(poll);
      if (dealsTimer) clearTimeout(dealsTimer);
      window.removeEventListener("hashchange", onHash);
    };
  }, []);

  const value = useMemo<StoreState>(
    () => ({
      deals,
      dealsLoading,
      certificates,
      queue,
      sary,
      activeStore,
      activeConsultant,
      setActiveStore,
      setActiveConsultant,
      addDeal: async (d, photos) => {
        setDeals((prev) => [d, ...prev]); // оптимистично
        // Сдача → очередь Эдвина только при «Получить от Mansband», не при «Выдано клиенту».
        if (USE_MOCK && d.changeStatus === "pending") {
          const changeAmt = Math.max(0, (d.paid ?? 0) - (d.total ?? 0) - (d.tips ?? 0));
          if (changeAmt > 0) {
            const q: QueueItem = {
              id: crypto.randomUUID(),
              kind: "change",
              dealNumber: d.number,
              client: d.clientName,
              amount: changeAmt,
              destination: d.changeDestination || "",
              status: "pending",
              issuedAmount: 0,
              payouts: [],
              createdAt: d.createdAt,
            };
            setQueue((prev) => [q, ...prev]);
          }
        }
        if (USE_MOCK && d.tips && d.tips > 0 && d.tipsDestination) {
          const tipsItem = {
            id: crypto.randomUUID(),
            kind: "tips",
            dealNumber: d.number,
            client: d.consultant,
            amount: d.tips,
            destination: d.tipsDestination,
            status: "pending",
            createdAt: d.createdAt,
          } as unknown as QueueItem;
          setQueue((prev) => [tipsItem, ...prev]);
        }
        if (USE_MOCK && d.returnStatus === "pending") {
          const via = d.returnPayouts?.length
            ? mansbandPayoutAmount(d.returnPayouts)
            : Math.abs(d.total ?? 0);
          if (via > 0) {
            setQueue((prev) => [
              {
                id: crypto.randomUUID(),
                kind: "refund",
                dealNumber: d.number,
                client: d.clientName,
                amount: via,
                destination: d.returnDestination || "",
                status: "pending",
                issuedAmount: 0,
                payouts: [],
                createdAt: d.createdAt,
              },
              ...prev,
            ]);
          }
        }
        if (USE_MOCK) return d;
        try {
          const saved = await api.post<Deal>("/deals", photos?.length ? { ...d, photos } : d);
          setDeals((prev) => prev.map((x) => (x.id === d.id ? saved : x)));
          // После продажи/оплаты сертификатом — обновить реестр балансов
          const touchedCert =
            d.kind === "cert_plastic" ||
            d.kind === "cert_digital" ||
            d.payments.some((p) => !!p.certificateNumber);
          if (touchedCert) {
            api.get<Certificate[]>("/certificates").then(setCertificates).catch(() => {});
          }
          return saved;
        } catch {
          return d;
        }
      },
      updateDealStage: (id, stage, reason) => {
        setDeals((prev) =>
          prev.map((d) =>
            d.id === id
              ? {
                  ...d,
                  stage,
                  history: [
                    ...(d.history ?? []),
                    {
                      at: new Date().toISOString(),
                      who: activeConsultant,
                      action: `Этап изменён: ${d.stage} → ${stage}${reason ? ` · Причина: ${reason}` : ""}`,
                    },
                  ],
                }
              : d
          )
        );
        if (!USE_MOCK) api.patch(`/deals/${id}/stage`, { stage, reason }).catch(() => {});
      },
      updateDeal: async (id, patch) => {
        const current = deals.find((d) => d.id === id);
        if (!current) return null;
        const { reason, ...rest } = patch;
        const next: Deal = {
          ...current,
          ...rest,
          history: [
            ...(current.history ?? []),
            {
              at: new Date().toISOString(),
              who: activeConsultant,
              action:
                rest.stage && rest.stage !== current.stage
                  ? `Этап изменён: ${current.stage} → ${rest.stage}${reason ? ` · Причина: ${reason}` : ""}`
                  : `Заявка обновлена${reason ? ` · ${reason}` : ""}`,
            },
          ],
        };
        setDeals((prev) => prev.map((d) => (d.id === id ? next : d)));
        if (USE_MOCK) return next;
        try {
          const saved = await api.patch<Deal>(`/deals/${current.number}`, { ...rest, reason });
          setDeals((prev) => prev.map((d) => (d.id === id ? saved : d)));
          return saved;
        } catch {
          return null;
        }
      },
      replaceDeal: (deal) => {
        setDeals((prev) => {
          const exists = prev.some((d) => d.id === deal.id || d.number === deal.number);
          return exists
            ? prev.map((d) => (d.id === deal.id || d.number === deal.number ? deal : d))
            : [deal, ...prev];
        });
      },
      handoverCompany: (id, patch) => {
        const parts = [
          patch.issued ? "товар выдан" : null,
          patch.documentsHanded ? "документы переданы" : null,
        ].filter(Boolean);
        setDeals((prev) =>
          prev.map((d) =>
            d.id === id
              ? {
                  ...d,
                  issued: patch.issued ?? d.issued,
                  documentsStatus: patch.documentsHanded ? "handed" : d.documentsStatus,
                  history: [
                    ...(d.history ?? []),
                    {
                      at: new Date().toISOString(),
                      who: activeConsultant,
                      action: `Выдача клиенту — ${parts.join(", ")}`,
                    },
                  ],
                }
              : d
          )
        );
        if (!USE_MOCK) api.patch(`/deals/${id}/handover`, patch).catch(() => {});
      },
      addDealComment: (id, text, who) => {
        setDeals((prev) =>
          prev.map((d) =>
            d.id === id
              ? {
                  ...d,
                  history: [
                    ...(d.history ?? []),
                    { at: new Date().toISOString(), who, action: `Комментарий: ${text}` },
                  ],
                }
              : d
          )
        );
        if (!USE_MOCK) api.post(`/deals/${id}/comments`, { text }).catch(() => {});
      },
      addQueueItem: (q) => {
        setQueue((prev) => [q, ...prev]);
        if (!USE_MOCK) api.post("/queue", q).catch(() => {});
      },
      issueQueueItem: (id, methodId, amount) => {
        setQueue((prev) =>
          prev.map((q) => {
            if (q.id !== id) return q;
            const already = q.issuedAmount ?? 0;
            const part = Math.min(amount ?? q.amount - already, q.amount - already);
            const issuedAmount = already + Math.max(0, part);
            const closed = issuedAmount >= q.amount;
            return {
              ...q,
              issuedAmount,
              payouts: [
                ...(q.payouts ?? []),
                {
                  id: crypto.randomUUID(),
                  amount: Math.max(0, part),
                  methodId,
                  issuedBy: activeConsultant,
                  issuedAt: new Date().toISOString(),
                },
              ],
              status: closed ? "issued" : "pending",
              issuedAt: closed ? new Date().toISOString() : undefined,
              issuedBy: activeConsultant,
              issueMethod: methodId,
              issueAccount: methodId,
            };
          })
        );
        if (!USE_MOCK) api.patch(`/queue/${id}/issue`, { methodId, amount }).catch(() => {});
      },
      reopenQueueItem: async (id) => {
        setQueue((prev) =>
          prev.map((q) =>
            q.id === id
              ? {
                  ...q,
                  status: "pending",
                  issuedAmount: 0,
                  payouts: [],
                  issuedAt: undefined,
                  issuedBy: undefined,
                  issueMethod: undefined,
                  issueAccount: undefined,
                }
              : q
          )
        );
        if (!USE_MOCK) {
          try {
            await api.patch(`/queue/${id}/reopen`, {});
          } catch {
            api.get<QueueItem[]>("/queue").then(setQueue).catch(() => {});
            throw new Error("Не удалось вернуть задачу в работу");
          }
        }
      },
      addSary: (input) => {
        const optimistic: SaryPayout = {
          id: crypto.randomUUID(),
          client: input.client,
          phone: input.phone,
          amount: input.amount,
          reason: input.reason,
          refDealNumber: input.refDealNumber,
          // Ручная выплата: заявки может не быть, номер проверяет сам колл-менеджер.
          seq: 1,
          phoneFound: true,
          status: "pending",
          screenshotAttached: false,
          createdAt: new Date().toISOString(),
        };
        setSary((prev) => [optimistic, ...prev]);
        if (!USE_MOCK) {
          api
            .post<SaryPayout>("/sary", input)
            .then((saved) => setSary((prev) => prev.map((s) => (s.id === optimistic.id ? saved : s))))
            .catch(() => setSary((prev) => prev.filter((s) => s.id !== optimistic.id)));
        }
      },
      markSarySent: () => {
        // Одиночная отметка без скрина запрещена — только пачка в разделе «Сары».
      },
      markSaryBatchSent: async (ids, screenshot, methodId) => {
        if (!screenshot?.contentBase64) {
          throw new Error("Приложите скриншот перевода");
        }
        if (!methodId) {
          throw new Error("Выберите, с какого счёта переведена пачка");
        }
        const set = new Set(ids);
        const sentAt = new Date().toISOString();
        let snapshot: SaryPayout[] = [];
        setSary((list) => {
          snapshot = list;
          return list.map((s) =>
            set.has(s.id) ? { ...s, status: "sent", screenshotAttached: true, sentAt } : s
          );
        });
        if (USE_MOCK) return;
        try {
          await api.patch("/sary/sent", { ids, screenshot, methodId });
        } catch (err) {
          setSary(snapshot);
          throw err;
        }
      },
      redeemCertificate: (number, amount) => {
        setCertificates((prev) =>
          prev.map((c) =>
            c.number === number
              ? {
                  ...c,
                  balance: Math.max(0, c.balance - amount),
                  status: Math.max(0, c.balance - amount) === 0 ? "used" : c.status,
                }
              : c
          )
        );
        if (!USE_MOCK) api.post("/certificates/redeem", { number, amount }).catch(() => {});
      },
      nextNumber: () => {
        let max = 1072;
        for (const d of deals) if (d.number > max) max = d.number;
        return max + 1;
      },
      findByPhone: (phone) => {
        const digits = phone.replace(/\D/g, "");
        if (digits.length < 10) return undefined;
        const tail = digits.slice(-10);
        return deals.find((d) => d.clientPhone.replace(/\D/g, "").includes(tail));
      },
      findSlivByPhone: (phone) => {
        const digits = phone.replace(/\D/g, "");
        if (digits.length < 10) return undefined;
        const tail = digits.slice(-10);
        return deals.find(
          (d) =>
            (d.kind === "sliv" || d.kind === "promise") &&
            d.clientPhone.replace(/\D/g, "").includes(tail)
        );
      },
    }),
    [deals, dealsLoading, certificates, queue, sary, activeStore, activeConsultant]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): StoreState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useStore must be used within StoreProvider");
  return v;
}
