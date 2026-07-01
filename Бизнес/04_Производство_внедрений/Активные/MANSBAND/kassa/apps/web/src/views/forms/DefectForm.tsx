import { useState } from "react";
import { useStore } from "../../store";
import { Card } from "../../components/ui";
import { ProductPicker } from "../../components/ProductPicker";
import {
  CommentField,
  ConsultantFields,
  FormShell,
  PhotoField,
  SectionTitle,
  StageActions,
  useSaved,
  type ConsultantData,
} from "./common";
import { STORE_ADDRESS } from "../../data/mock";
import { KIND_LABEL } from "../../lib/labels";
import { attachmentsToUpload, type PhotoAttachment } from "../../lib/photo";
import type { CartItem, DealKind } from "../../data/types";

type DefectKind = "defect" | "drycleaning" | "resew" | "wrong_size";

const CONFIG: Record<DefectKind, { subtitle: string; needPhoto: boolean; note: string }> = {
  defect: {
    subtitle: "Дефекты · брак товара",
    needPhoto: true,
    note: "Товар авто-перемещается на склад «Брак». Фото дефекта обязательно.",
  },
  drycleaning: {
    subtitle: "Дефекты · химчистка",
    needPhoto: true,
    note: "Контроль возврата из химчистки. Фото состояния обязательно.",
  },
  resew: {
    subtitle: "Дефекты · перешив (ателье)",
    needPhoto: false,
    note: "Перешив в ателье. Укажите плановую дату возврата в комментарии.",
  },
  wrong_size: {
    subtitle: "Дефекты · перепутан размер комплекта",
    needPhoto: false,
    note: "Фиксация пересортицы размеров. Фото не требуется.",
  },
};

export function DefectForm({ kind, onDone }: { kind: DealKind; onDone: () => void }) {
  const k = kind as DefectKind;
  const cfg = CONFIG[k] ?? CONFIG.defect;
  const { activeStore, activeConsultant, addDeal, nextNumber } = useStore();
  const { saved, setSaved, toast } = useSaved();
  const [meta] = useState(() => ({ number: nextNumber(), createdAt: new Date().toISOString() }));

  const [consultants, setConsultants] = useState<ConsultantData>({ consultant: activeConsultant, referredBy: "" });
  const [items, setItems] = useState<CartItem[]>([]);
  const [comment, setComment] = useState("");
  const [photos, setPhotos] = useState<PhotoAttachment[]>([]);

  const baseFilled = !!(items.length > 0 && comment.trim() && (cfg.needPhoto ? photos.length > 0 : true));

  function save(s: string) {
    addDeal({
      id: crypto.randomUUID(),
      number: meta.number,
      createdAt: meta.createdAt,
      funnel: "defects",
      kind,
      consultant: consultants.consultant,
      clientName: "—",
      clientPhone: "—",
      store: activeStore,
      storeAddress: STORE_ADDRESS[activeStore],
      items: items.map((it) => ({ ...it, price: 0, noPrice: true })),
      payments: [],
      stage: s,
      comment,
      photoAttached: cfg.needPhoto ? photos.length > 0 : undefined,
      total: 0,
      paid: 0,
    }, attachmentsToUpload(photos));
    setSaved(true);
    setTimeout(onDone, 1100);
  }

  return (
    <FormShell
      title={KIND_LABEL[kind]}
      subtitle={cfg.subtitle}
      onBack={onDone}
      meta={meta}
      storeAddress={STORE_ADDRESS[activeStore]}
      footer={
        <StageActions
          stages={["Новая заявка", "Взято в работу"]}
          stage="Взято в работу"
          onStageChange={() => {}}
          onSave={save}
          saved={saved}
          disabled={!baseFilled}
          onBack={onDone}
          successHint={!baseFilled ? (cfg.needPhoto && photos.length === 0 ? "Нужны позиции, комментарий и фото" : "Нужны позиции и комментарий") : undefined}
        />
      }
    >
      <Card>
        <SectionTitle>Консультант</SectionTitle>
        <ConsultantFields data={consultants} onChange={setConsultants} />
      </Card>

      <Card>
        <SectionTitle>Позиции с дефектом</SectionTitle>
        <ProductPicker items={items} onChange={setItems} noPrice />
      </Card>

      <Card>
        <SectionTitle>Комментарий</SectionTitle>
        <CommentField value={comment} onChange={setComment} required />
      </Card>

      {cfg.needPhoto && (
        <Card>
          <SectionTitle>Фото дефекта</SectionTitle>
          <PhotoField photos={photos} onChange={setPhotos} label="Фото дефекта" />
        </Card>
      )}

      {toast}
    </FormShell>
  );
}
