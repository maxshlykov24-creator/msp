import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { Certificate, Deal, QueueItem, SaryPayout, Store } from "./data/types";
import { CERTIFICATES, DEALS, QUEUE, SARY } from "./data/mock";

interface StoreState {
  deals: Deal[];
  certificates: Certificate[];
  queue: QueueItem[];
  sary: SaryPayout[];
  activeStore: Store;
  activeConsultant: string;
  setActiveStore: (s: Store) => void;
  setActiveConsultant: (c: string) => void;
  addDeal: (d: Deal) => void;
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
  const [deals, setDeals] = useState<Deal[]>(DEALS);
  const [certificates, setCertificates] = useState<Certificate[]>(CERTIFICATES);
  const [queue, setQueue] = useState<QueueItem[]>(QUEUE);
  const [sary, setSary] = useState<SaryPayout[]>(SARY);
  const [activeStore, setActiveStore] = useState<Store>("На Бауманской");
  const [activeConsultant, setActiveConsultant] = useState<string>("Матвей");

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
      addDeal: (d) => setDeals((prev) => [d, ...prev]),
      updateDealStage: (id, stage) =>
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
        ),
      addDealComment: (id, text, who) =>
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
        ),
      addQueueItem: (q) => setQueue((prev) => [q, ...prev]),
      issueQueueItem: (id) =>
        setQueue((prev) =>
          prev.map((q) => (q.id === id ? { ...q, status: "issued" } : q))
        ),
      markSarySent: (id) =>
        setSary((prev) =>
          prev.map((s) => (s.id === id ? { ...s, status: "sent", screenshotAttached: true } : s))
        ),
      redeemCertificate: (number, amount) =>
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
        ),
      nextNumber: () =>
        Math.max(...deals.map((d) => d.number), 1072) + 1,
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
