import { useEffect, useState } from "react";
import { UserCheck } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import { ProductPicker, cartTotal } from "../../components/ProductPicker";
import { DealActionsBar } from "../../components/DealActionsBar";
import { MovementModal, defaultMovementTarget } from "../../components/MovementModal";
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
  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Дано обещание");
  const [moveOpen, setMoveOpen] = useState(false);
  const [persistedNumber, setPersistedNumber] = useState<number | null>(null);

  const missingRequired = [
    !client.phone && "Телефон клиента",
    !client.name && "Имя клиента",
    items.length === 0 && "Товары",
    !actualUntil && "Актуально до",
    !comment.trim() && "Комментарий",
  ].filter(Boolean) as string[];
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
    if (!baseFilled) return null;
    if (persistedNumber != null) {
      return { dealNumber: persistedNumber, store: activeStore };
    }
    const savedDeal = await addDeal(buildDeal("Ждет товар"));
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
      headerActions={
        <DealActionsBar onMovement={items.length > 0 ? () => setMoveOpen(true) : undefined} />
      }
      footer={
        <StageActions
          stages={["Дано обещание", "Хочет прийти", "Ждет товар"]}
          stage={stage}
          onStageChange={setStage}
          onSave={(s) => void save(s)}
          saved={saved}
          disabled={!baseFilled}
        />
      }
    >
      <MovementModal
        open={moveOpen}
        onClose={() => setMoveOpen(false)}
        dealNumber={persistedNumber ?? undefined}
        store={activeStore}
        items={items}
        defaultTarget={defaultMovementTarget(activeStore)}
        ensureDeal={persistedNumber == null ? () => ensureDealForMove() : undefined}
        onCreated={() => {
          setStage("Ждет товар");
          setSaved(true);
          setTimeout(onDone, 600);
        }}
      />

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
