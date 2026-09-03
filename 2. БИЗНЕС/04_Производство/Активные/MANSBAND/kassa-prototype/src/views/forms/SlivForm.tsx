import { useState } from "react";
import { TrendingUp } from "lucide-react";
import { useStore } from "../../store";
import { Card, Field } from "../../components/ui";
import {
  ClientFields,
  CommentField,
  ConsultantFields,
  FormShell,
  SectionTitle,
  StageActions,
  useSaved,
  type ClientData,
  type ConsultantData,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";

const SLIV_STAGES = ["Встреча назначена", "Провал"];

export function SlivForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });
  const [meetingDate, setMeetingDate] = useState("");
  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Встреча назначена");

  const baseFilled = !!(client.phone && comment.trim());

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "sliv",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      clientName: client.name || "Клиент (слив)",
      clientPhone: client.phone,
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      meetingDate: meetingDate || undefined,
      items: [],
      payments: [],
      stage: s,
      comment,
      total: 0,
      paid: 0,
    });
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title="Слив (перенаправление клиента)"
      subtitle="Передать клиента в другой шоурум — конверсия защитит обоих консультантов"
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      footer={
        <StageActions
          stages={SLIV_STAGES}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          onBack={onDone}
          successHint={!baseFilled ? "Заполните телефон и комментарий" : undefined}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант и клиент</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} />
        </div>
        <div className="mt-4 max-w-[220px]">
          <Field label="Дата встречи">
            <input type="date" className="input" value={meetingDate} onChange={(e) => setMeetingDate(e.target.value)} />
          </Field>
        </div>
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} required />
        </div>
      </Card>

      <Card>
        <div className="flex items-center gap-3">
          <TrendingUp className="text-white" size={20} />
          <div>
            <div className="text-white font-semibold">Конверсия консультанта {consultants.consultant}</div>
            <div className="text-mute text-sm">
              Слив убирает клиента из знаменателя конверсии — не ухудшает и не улучшает показатель; за слив капают бонусы.
            </div>
          </div>
        </div>
      </Card>
      {toast}
    </FormShell>
  );
}
