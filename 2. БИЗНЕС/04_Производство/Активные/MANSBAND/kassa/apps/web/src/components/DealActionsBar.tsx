import { ArrowRightLeft, ClipboardList, History, Repeat2 } from "lucide-react";

/** Кнопки над блоком «Консультант и клиент»: задача / провести как / история / перемещение. */
export function DealActionsBar({
  onTask,
  onConvert,
  onHistory,
  onMovement,
  historyCount = 0,
}: {
  onTask?: () => void;
  /** Отложка/обещание → продажа / компания / аренда. */
  onConvert?: () => void;
  onHistory?: () => void;
  onMovement?: () => void;
  historyCount?: number;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {onTask && (
        <button
          type="button"
          onClick={onTask}
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] font-medium text-white hover:border-gold/40"
        >
          <ClipboardList size={15} className="text-gold" /> Создать задачу
        </button>
      )}
      {onConvert && (
        <button
          type="button"
          onClick={onConvert}
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] font-medium text-white hover:border-gold/40"
        >
          <Repeat2 size={15} className="text-gold" /> Провести как…
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
      {onMovement && (
        <button
          type="button"
          onClick={onMovement}
          className="inline-flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-[13px] font-medium text-gold-soft hover:text-white hover:border-gold/40"
        >
          <ArrowRightLeft size={15} /> Создать перемещение
        </button>
      )}
    </div>
  );
}
