import { useState } from "react";
import {
  ShoppingBag, Building2, Ticket, Shirt, Clock, HandHeart, XCircle, Send,
  Mail, Truck, AlertTriangle, Droplets, Scissors, Replace, Undo2, RefreshCw,
} from "lucide-react";
import type { DealKind, FunnelType } from "../data/types";
import { FUNNEL_LABEL, KIND_LABEL } from "../lib/labels";
import { SaleForm } from "./forms/SaleForm";
import { CompanyForm } from "./forms/CompanyForm";
import { CertificateForm } from "./forms/CertificateForm";
import { RentalForm } from "./forms/RentalForm";
import { SlivForm } from "./forms/SlivForm";
import { DeferredForm } from "./forms/DeferredForm";
import { PromiseForm } from "./forms/PromiseForm";
import { NoSlivForm } from "./forms/NoSlivForm";
import { DeliveryForm } from "./forms/DeliveryForm";
import { DefectForm } from "./forms/DefectForm";
import { RefundForm } from "./forms/RefundForm";
import { ExchangeForm } from "./forms/ExchangeForm";

const KINDS: Record<FunnelType, { kind: DealKind; icon: typeof ShoppingBag; live?: boolean }[]> = {
  offline: [
    { kind: "sale", icon: ShoppingBag, live: true },
    { kind: "company", icon: Building2, live: true },
    { kind: "cert_plastic", icon: Ticket, live: true },
    { kind: "rental", icon: Shirt, live: true },
    { kind: "deferred", icon: Clock, live: true },
    { kind: "promise", icon: HandHeart, live: true },
    { kind: "no_sliv", icon: XCircle, live: true },
    { kind: "sliv", icon: Send, live: true },
  ],
  online: [
    { kind: "cert_digital", icon: Mail, live: true },
    { kind: "delivery", icon: Truck, live: true },
  ],
  defects: [
    { kind: "defect", icon: AlertTriangle, live: true },
    { kind: "drycleaning", icon: Droplets, live: true },
    { kind: "resew", icon: Scissors, live: true },
    { kind: "wrong_size", icon: Replace, live: true },
  ],
  return: [
    { kind: "refund", icon: Undo2, live: true },
    { kind: "exchange", icon: RefreshCw, live: true },
  ],
};

export function NewDeal({ onClose, initialKind }: { onClose: () => void; initialKind?: DealKind | null }) {
  const [funnel, setFunnel] = useState<FunnelType>("offline");
  const [kind, setKind] = useState<DealKind | null>(initialKind ?? null);

  if (kind === "sale") return <SaleForm onDone={onClose} />;
  if (kind === "company") return <CompanyForm onDone={onClose} />;
  if (kind === "cert_plastic") return <CertificateForm onDone={onClose} />;
  if (kind === "cert_digital") return <CertificateForm onDone={onClose} digital />;
  if (kind === "rental") return <RentalForm onDone={onClose} />;
  if (kind === "sliv") return <SlivForm onDone={onClose} />;
  if (kind === "deferred") return <DeferredForm onDone={onClose} />;
  if (kind === "promise") return <PromiseForm onDone={onClose} />;
  if (kind === "no_sliv") return <NoSlivForm onDone={onClose} />;
  if (kind === "delivery") return <DeliveryForm onDone={onClose} />;
  if (kind === "defect" || kind === "drycleaning" || kind === "resew" || kind === "wrong_size")
    return <DefectForm kind={kind} onDone={onClose} />;
  if (kind === "refund") return <RefundForm onDone={onClose} />;
  if (kind === "exchange") return <ExchangeForm onDone={onClose} />;

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-2xl font-extrabold text-white mb-1">Новая заявка</h1>
      <p className="text-mute text-sm mb-5">Выберите тип воронки и вид заявки</p>

      <div className="inline-flex rounded-lg border border-ink-700 overflow-hidden mb-6 flex-wrap">
        {(Object.keys(FUNNEL_LABEL) as FunnelType[]).map((f) => (
          <button
            key={f}
            onClick={() => setFunnel(f)}
            className={`px-4 py-2.5 text-[14px] font-semibold ${
              funnel === f ? "bg-gold text-ink-950" : "text-mute hover:bg-ink-800"
            }`}
          >
            {FUNNEL_LABEL[f]}
          </button>
        ))}
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {KINDS[funnel].map(({ kind: k, icon: Icon, live }) => (
          <button
            key={k}
            onClick={() => setKind(k)}
            className="card p-5 text-left hover:border-gold/50 hover:shadow-glow transition group relative"
          >
            {live && (
              <span className="absolute top-3 right-3 chip bg-white/12 text-white">demo</span>
            )}
            <div className="w-11 h-11 rounded-xl bg-ink-800 group-hover:bg-gold/15 grid place-items-center mb-3 transition">
              <Icon size={22} className="text-gold" />
            </div>
            <div className="text-white font-bold">{KIND_LABEL[k]}</div>
            <div className="text-[12px] text-mute mt-0.5">{FUNNEL_LABEL[funnel]}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
