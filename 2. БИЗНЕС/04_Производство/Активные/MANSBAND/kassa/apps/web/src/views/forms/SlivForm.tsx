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
import { Hint } from "../../lib/hints";

export function SlivForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });
  const [meetingDate, setMeetingDate] = useState("");
  const [comment, setComment] = useState("");
  const [stage, setStage] = useState("Провал");
  const stages = meetingDate ? ["Встреча назначена", "Провал"] : ["Провал"];

  function handleMeetingDate(value: string) {
    setMeetingDate(value);
    setStage(value ? "Встреча назначена" : "Провал");
  }

  // На «Провале» телефон не нужен — консультант фиксирует только факт и комментарий.
  const phoneRequired = stage === "Встреча назначена";
  const missingRequired = [
    phoneRequired && !client.phone && "Телефон",
    !comment.trim() && "Комментарий",
  ].filter(Boolean) as string[];
  const baseFilled = missingRequired.length === 0;

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
      onBack={onDone}
      title="Слив"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={stages}
          stage={stage}
          onStageChange={setStage}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
        />
      }
    >
      <Hint>
        Передать клиента в другой шоурум: конверсия защитит обоих консультантов. Слив, добитый в
        другом магазине, не портит конверсию исходного.
      </Hint>
      <Card>
        <SectionTitle>Консультант и клиент</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
        <div className="mt-4">
          <ClientFields
            data={client}
            onChange={setClient}
            phoneRequired={phoneRequired}
            nameRequired={phoneRequired}
          />
        </div>
        <div className="mt-4 max-w-[220px]">
          <Field label="Дата встречи" hint="Без даты заявка может быть сохранена только в «Провал»">
            <input type="date" className="input" value={meetingDate} onChange={(e) => handleMeetingDate(e.target.value)} />
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
