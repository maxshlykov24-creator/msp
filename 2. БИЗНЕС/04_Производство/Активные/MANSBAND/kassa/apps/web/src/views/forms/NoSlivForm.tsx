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

export function NoSlivForm({ onDone, onBack = onDone }: { onDone: () => void; onBack?: () => void }) {
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, saving, runSave, toast } = useSaved();
  const [meta] = useState(() => ({ id: crypto.randomUUID(), number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
  const [client, setClient] = useState<ClientData>({ name: "", phone: "", channel: "", purpose: "" });
  const [comment, setComment] = useState("");

  const missingRequired = [!client.name.trim() && "Имя клиента", client.phone.replace(/\D/g, "").length < 10 && "Телефон клиента", !comment.trim() && "Комментарий"].filter(Boolean) as string[];
  const baseFilled = missingRequired.length === 0;

  function save(s: string) {
    void runSave(async () => {
      await addDeal({
        id: meta.id,
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
    }, onDone);
  }

  return (
    <FormShell
      onBack={onBack}
      title="Не слив"
      subtitle="Оффлайн"
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      missingRequired={missingRequired}
      footer={
        <StageActions
          stages={["Не слив"]}
          stage="Не слив"
          onStageChange={() => {}}
          onSave={save}
          saved={saved || saving}
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
