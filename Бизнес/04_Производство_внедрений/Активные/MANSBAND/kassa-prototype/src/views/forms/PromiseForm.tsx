import { useEffect, useState } from "react";
import { UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal } from "../../components/ProductPicker";
import {
  ClientFields,
  CommentField,
  ConsultantFields,
  FormShell,
  SectionTitle,
  SourceFields,
  StageActions,
  todayStr,
  useSaved,
  type ClientData,
  type ConsultantData,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import type { CartItem, Deal } from "../../data/types";

function addWeeks(dateStr: string, weeks: number): string {
  try {
    const d = new Date(dateStr);
    d.setDate(d.getDate() + weeks * 7);
    return d.toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

export function PromiseForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber, findSlivByPhone } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });

  const [slivFound, setSlivFound] = useState<Deal | null>(null);
  useEffect(() => {
    const digits = client.phone.replace(/\D/g, "");
    if (digits.length !== 11) {
      setSlivFound(null);
      return;
    }
    const found = findSlivByPhone(client.phone);
    if (found && found.id !== slivFound?.id) {
      setSlivFound(found);
      setConsultants((prev) => (prev.referredBy ? prev : { ...prev, referredBy: found.consultant }));
    } else if (!found) {
      setSlivFound(null);
    }
  }, [client.phone]);

  const [items, setItems] = useState<CartItem[]>([]);
  const [actualUntil, setActualUntil] = useState(addWeeks(todayStr(), 2));
  const [comment, setComment] = useState("");

  const baseFilled = !!(client.name && client.phone && items.length > 0 && actualUntil && client.channel && comment.trim());

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "promise",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      clientName: client.name,
      clientPhone: client.phone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      channel: client.channel,
      purpose: client.purpose,
      items,
      payments: [],
      stage: s,
      actualUntil,
      comment,
      total: cartTotal(items),
      paid: 0,
    });
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title="Обещание"
      subtitle="Оффлайн · клиент обещал вернуться за товаром"
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      footer={
        <StageActions
          stages={["Дано обещание"]}
          stage="Дано обещание"
          onStageChange={() => {}}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          onBack={onDone}
          successHint={!baseFilled ? "Заполните клиента, товары, срок, источник и комментарий" : "Этап «Дано обещание» — авто"}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант и клиент</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} />
        </div>
        {slivFound && (
          <div className="mt-3 flex items-center gap-2 text-[12px] text-emerald-300/90 bg-emerald-400/10 border border-emerald-400/20 rounded px-3 py-2">
            <UserCheck size={13} /> Пришёл по сливу: заявка #{slivFound.number} · направил {slivFound.consultant}
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>Интересующие товары</SectionTitle>
        <ProductPicker items={items} onChange={setItems} />
        <div className="mt-4 max-w-[220px]">
          <Field label="Актуально до" required>
            <input type="date" className="input" value={actualUntil} onChange={(e) => setActualUntil(e.target.value)} />
          </Field>
        </div>
      </Card>

      <Card>
        <SectionTitle>Источник и комментарий</SectionTitle>
        <SourceFields data={client} onChange={setClient} withPurpose={false} />
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} required />
        </div>
      </Card>

      {toast}
    </FormShell>
  );
}
