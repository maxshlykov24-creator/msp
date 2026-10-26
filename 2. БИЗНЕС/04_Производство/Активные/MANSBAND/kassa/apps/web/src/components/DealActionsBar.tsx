import { ExternalLink, History, Repeat2 } from "lucide-react";
import { DealTasksButton } from "./DealTasksButton";
import type { CreatedTask } from "./CreateTaskForm";

/** Кнопки над блоком «Консультант и клиент»: задача / провести как / история. */
export function DealActionsBar({
  tasks,
  amoLeadId,
  onConvert,
  onHistory,
  historyCount = 0,
}: {
  tasks?: {
    dealNumber?: number;
    store: string;
    /** Не грузить список, пока заявки ещё нет на сервере. */
    load?: boolean;
    onCreated?: (task: CreatedTask) => void;
  };
  /** Сделка amoCRM. Кнопка есть только у уже созданной заявки со связью. */
  amoLeadId?: number;
  /** Отложка/обещание → продажа / компания / аренда. */
  onConvert?: () => void;
  onHistory?: () => void;
  historyCount?: number;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {tasks && (
        <DealTasksButton
          dealNumber={tasks.dealNumber}
          store={tasks.store}
          load={tasks.load}
          onCreated={tasks.onCreated}
        />
      )}
      {amoLeadId != null && amoLeadId > 0 && (
        <a
          href={`https://mansband.amocrm.ru/leads/detail/${amoLeadId}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] font-medium text-white hover:border-gold/40"
        >
          <ExternalLink size={15} className="text-gold" /> Открыть в амо
        </a>
      )}
      {onConvert && (
        <button
          type="button"
          onClick={onConvert}
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] font-medium text-white hover:border-gold/40"
        >
          <Repeat2 size={15} className="text-gold" /> Сменить тип
        </button>
      )}
      {onHistory && (
        <button
          type="button"
          onClick={onHistory}
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] font-medium text-mute-soft hover:text-white hover:border-gold/40"
        >
          <History size={15} /> История{historyCount > 0 ? ` · ${historyCount}` : ""}
        </button>
      )}
    </div>
  );
}
