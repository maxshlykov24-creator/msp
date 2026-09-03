import { useState } from "react";
import { useStore } from "../../store";
import { Card } from "../../components/ui";
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

export function NoSlivForm({ onDone }: { onDone: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });
  const [comment, setComment] = useState("");

  const missingRequired = [!comment.trim() && "Комментарий"].filter(Boolean) as string[];
  const baseFilled = missingRequired.length === 0;

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "offline",
      kind: "no_sliv",
      consultant: consultants.consultant,
      referredBy: consultants.referredBy || undefined,
      clientName: client.name || "Клиент (не слив)",
      clientPhone: client.phone || "—",
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
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
      title="Не слив"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={["Провал"]}
          stage="Провал"
          onStageChange={() => {}}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
        <div className="mt-4">
          <ClientFields data={client} onChange={setClient} />
        </div>
        <div className="mt-4">
          <CommentField value={comment} onChange={setComment} required />
        </div>
      </Card>

      {toast}
    </FormShell>
  );
}
