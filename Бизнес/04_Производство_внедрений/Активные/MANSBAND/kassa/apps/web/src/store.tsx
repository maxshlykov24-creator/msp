import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Certificate, Deal, QueueItem, SaryPayout, Store } from "./data/types";
import { CERTIFICATES, DEALS, QUEUE, SARY } from "./data/mock";
import { api, USE_MOCK } from "./api/client";
import { connectWs } from "./api/ws";
import type { PhotoUploadItem } from "./lib/photo";

interface StoreState {
  deals: Deal[];
  certificates: Certificate[];
  queue: QueueItem[];
  sary: SaryPayout[];
  activeStore: Store;
  activeConsultant: string;
  setActiveStore: (s: Store) => void;
  setActiveConsultant: (c: string) => void;
  addDeal: (d: Deal, photos?: PhotoUploadItem[]) => void;
  updateDealStage: (id: string, stage: string) => void;
  addDealComment: (id: string, text: string, who: string) => void;
  addQueueItem: (q: QueueItem) => void;
  issueQueueItem: (id: string) => void;
  markSarySent: (id: string) => void;
  redeemCertificate: (number: string, amount: number) => void;
  nextNumber: () => number;
  findByPhone: (phone: string) => Deal | undefined;
  findSlivByPhone: (phone: string) => Deal | undefined;
}

const Ctx = createContext<StoreState | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [deals, setDeals] = useState<Deal[]>(USE_MOCK ? DEALS : []);
  const [certificates, setCertificates] = useState<Certificate[]>(USE_MOCK ? CERTIFICATES : []);
  const [queue, setQueue] = useState<QueueItem[]>(USE_MOCK ? QUEUE : []);
  const [sary, setSary] = useState<SaryPayout[]>(USE_MOCK ? SARY : []);
  const [activeStore, setActiveStore] = useState<Store>("На Бауманской");
  const [activeConsultant, setActiveConsultant] = useState<string>("Матвей");

  // Загрузка реальных данных + подписка на WebSocket (только не в мок-режиме).
  useEffect(() => {
    if (USE_MOCK) return;
    const reloadDeals = () => api.get<Deal[]>("/deals").then(setDeals).catch(() => {});
    const reloadCerts = () =>
      api.get<Certificate[]>("/certificates").then(setCertificates).catch(() => {});
    const reloadQueue = () => api.get<QueueItem[]>("/queue").then(setQueue).catch(() => {});
    const reloadSary = () => api.get<SaryPayout[]>("/sary").then(setSary).catch(() => {});

    reloadDeals();
    reloadCerts();
    reloadQueue();
    reloadSary();

    const disconnect = connectWs((ev) => {
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
        default:
          break;
      }
    });

    // Поллинг-фолбэк, если webhook/WS не дошёл.
    const poll = setInterval(reloadDeals, 60_000);
    return () => {
      disconnect();
      clearInterval(poll);
    };
  }, []);

  const value = useMemo<StoreState>(
    () => ({
      deals,
      certificates,
      queue,
      sary,
      activeStore,
      activeConsultant,
      setActiveStore,
      setActiveConsultant,
      addDeal: (d, photos) => {
        setDeals((prev) => [d, ...prev]); // оптимистично
        if (!USE_MOCK) {
          api
            .post<Deal>("/deals", photos?.length ? { ...d, photos } : d)
            .then((saved) => setDeals((prev) => prev.map((x) => (x.id === d.id ? saved : x))))
            .catch(() => {});
        }
      },
      updateDealStage: (id, stage) => {
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
                      action: `Этап изменён: ${d.stage} → ${stage}`,
                    },
                  ],
                }
              : d
          )
        );
        if (!USE_MOCK) api.patch(`/deals/${id}/stage`, { stage }).catch(() => {});
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
      issueQueueItem: (id) => {
        setQueue((prev) => prev.map((q) => (q.id === id ? { ...q, status: "issued" } : q)));
        if (!USE_MOCK) api.patch(`/queue/${id}/issue`).catch(() => {});
      },
      markSarySent: (id) => {
        setSary((prev) =>
          prev.map((s) => (s.id === id ? { ...s, status: "sent", screenshotAttached: true } : s))
        );
        if (!USE_MOCK) api.patch(`/sary/${id}/sent`).catch(() => {});
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
      nextNumber: () => Math.max(...deals.map((d) => d.number), 1072) + 1,
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
    [deals, certificates, queue, sary, activeStore, activeConsultant]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): StoreState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useStore must be used within StoreProvider");
  return v;
}
