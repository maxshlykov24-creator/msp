import { DatePicker } from "../../components/DatePicker";
import { isApproxDateStage } from "@kassa/shared";
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
  movementRequiredGap,
  todayStr,
  useSaved,
  type ClientData,
  type ConsultantData,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { useAuth } from "../../auth/AuthContext";
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
  const { user } = useAuth();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));
  const [dealId] = useState(() => crypto.randomUUID());

  // Обещание от колл-менеджера (созвон 20.08): консультант пуст до продажи.
  const isCallManager = user?.role === "crm";
  const [consultants, setConsultants] = useState<ConsultantData>({
    consultant: isCallManager ? "" : activeConsultant,
    referredBy: "",
    callManager: isCallManager ? user?.name : undefined,
  });
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
  const [approxMeetingDate, setApproxMeetingDate] = useState("");
  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Дано обещание");
  const [persistedNumber, setPersistedNumber] = useState<number | null>(null);

  const [movementCreated, setMovementCreated] = useState(false);
  const missingRequired = [
    !client.phone && "Телефон клиента",
    !client.name && "Имя клиента",
    items.length === 0 && "Товары",
    !actualUntil && "Актуально до",
    !comment.trim() && "Комментарий",
    isApproxDateStage(stage) && !approxMeetingDate && "Примерная дата встречи или заказа",
    movementRequiredGap(stage, movementCreated),
  ].filter(Boolean) as string[];
  const fieldsFilled = missingRequired.filter((item) => item !== "Задача на перемещение").length === 0;
  const baseFilled = missingRequired.length === 0;

  function buildDeal(s: string): Deal {
    return {
      id: dealId,
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "promise",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      callManager: consultants.callManager || undefined,
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
      approxMeetingDate: isApproxDateStage(s) ? approxMeetingDate : undefined,
      comment,
      total: cartTotal(items),
      paid: 0,
    };
  }

  async function save(s: string) {
    const savedDeal = await addDeal(buildDeal(s));
    setPersistedNumber(savedDeal.number);
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  async function ensureDealForMove() {
    if (!fieldsFilled) return null;
    if (persistedNumber != null) {
      return { dealNumber: persistedNumber, store: activeStore };
    }
    const savedDeal = await addDeal(buildDeal("Ждет товар"), undefined, { strict: true });
    setPersistedNumber(savedDeal.number);
    setStage("Ждет товар");
    setSaved(true);
    return { dealNumber: savedDeal.number, store: savedDeal.store || activeStore };
  }

  return (
    <FormShell
      onBack={onDone}
      title="Обещание"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={["Дано обещание", "Хочет прийти", "Хочет заказать", "Ждет товар"]}
          stage={stage}
          onStageChange={setStage}
          onSave={(s) => void save(s)}
          saved={saved}
          disabled={!baseFilled}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант и клиент</SectionTitle>
        <ConsultantFields
          data={consultants}
          onChange={setConsultants}
          allowEmptyConsultant={isCallManager}
        />
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
        <ProductPicker
          items={items}
          onChange={setItems}
          dealNumber={persistedNumber ?? undefined}
          dealKind="promise"
          ensureDeal={persistedNumber == null ? () => ensureDealForMove() : undefined}
          onMovementCreated={() => {
            setMovementCreated(true);
            setStage("Ждет товар");
            setSaved(true);
            setTimeout(onDone, 600);
          }}
        />
        <div className="mt-4 max-w-[220px]">
          <Field label="Актуально до" required>
            <DatePicker className="input" value={actualUntil} onChange={(value) => setActualUntil(value)} />
          </Field>
          {isApproxDateStage(stage) && (
            <Field label="Примерная дата встречи или заказа" required>
              <DatePicker className="input" value={approxMeetingDate} onChange={(value) => setApproxMeetingDate(value)} />
            </Field>
          )}
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
