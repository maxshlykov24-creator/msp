import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  LayoutList,
  PlusCircle,
  Send,
  BarChart3,
  Wallet,
  Gift,
  Store as StoreIcon,
  ChevronDown,
  ChevronRight,
  Users,
  ArrowLeft,
  Check,
  AlertTriangle,
} from "lucide-react";
import { useStore } from "../store";
import { CONSULTANTS, STORES } from "../data/mock";
import logoFull from "../assets/logo-full.png";

export type Route =
  | "board"
  | "new"
  | "certificates"
  | "sary"
  | "queue"
  | "shift"
  | "dashboard";

// Подпункты «Все заявки» — открывают доску с пресетом группы (статус: все).
const ALL_SUB: { group: string; label: string; status: string }[] = [
  { group: "all", label: "Все", status: "all" },
  { group: "sale", label: "Продажи", status: "all" },
  { group: "rental", label: "Аренда", status: "all" },
  { group: "cert", label: "Сертификаты", status: "all" },
  { group: "company", label: "Продажи компании", status: "all" },
  { group: "delivery", label: "Доставки", status: "all" },
];

// Подпункты «Дефекты» — по умолчанию фильтр «открытые».
const DEFECT_SUB: { group: string; label: string; status: string }[] = [
  { group: "defect_all", label: "Все", status: "open" },
  { group: "defect", label: "Браки", status: "open" },
  { group: "drycleaning", label: "Химчистка", status: "open" },
  { group: "resew", label: "Перешив", status: "open" },
  { group: "wrong_size", label: "Перепутан размер", status: "open" },
];

// Нижнее меню на мобильном: 4 основных действия (крупные кнопки).
const MOBILE_NAV: { id: Route; label: string; icon: ReactNode }[] = [
  { id: "new", label: "Новая", icon: <PlusCircle size={23} /> },
  { id: "board", label: "Заявки", icon: <LayoutList size={23} /> },
  { id: "sary", label: "Сары", icon: <Gift size={23} /> },
  { id: "shift", label: "Смена", icon: <Wallet size={23} /> },
];

export function Layout({
  route,
  setRoute,
  onBoard,
  boardGroup,
  children,
}: {
  route: Route;
  setRoute: (r: Route) => void;
  onBoard: (group: string, status: string) => void;
  boardGroup: string | null;
  children: ReactNode;
}) {
  const { activeStore, setActiveStore, activeConsultant, setActiveConsultant, queue } = useStore();
  const pendingQueue = queue.filter((q) => q.status === "pending").length;

  return (
    <div className="min-h-screen flex w-full overflow-x-hidden">
      {/* Sidebar */}
      <aside className="hidden md:flex w-64 shrink-0 flex-col border-r border-ink-800 bg-ink-900/60 backdrop-blur sticky top-0 h-screen">
        <div className="px-5 py-6 border-b border-ink-800">
          <img src={logoFull} alt="MANSBAND" className="h-14 w-auto select-none" />
          <div className="text-[11px] text-mute tracking-[0.28em] uppercase mt-2.5 pl-0.5">Касса</div>
        </div>
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          <NavButton
            active={route === "new"}
            icon={<PlusCircle size={18} />}
            label="Новая заявка"
            onClick={() => setRoute("new")}
          />
          <NavGroup
            icon={<LayoutList size={18} />}
            label="Все заявки"
            items={ALL_SUB}
            activeGroup={route === "board" ? (boardGroup ?? "all") : null}
            defaultOpen
            onPick={onBoard}
          />
          <NavGroup
            icon={<AlertTriangle size={18} />}
            label="Дефекты"
            items={DEFECT_SUB}
            activeGroup={route === "board" ? (boardGroup ?? "all") : null}
            onPick={onBoard}
          />
          <NavButton
            active={route === "sary"}
            icon={<Gift size={18} />}
            label="Сары"
            onClick={() => setRoute("sary")}
          />
          <NavButton
            active={route === "queue"}
            icon={<Send size={18} />}
            label="Очередь Эдвина"
            onClick={() => setRoute("queue")}
            badge={pendingQueue}
          />
          <NavButton
            active={route === "shift"}
            icon={<Wallet size={18} />}
            label="Закрытие смены"
            onClick={() => setRoute("shift")}
          />
          <NavButton
            active={route === "dashboard"}
            icon={<BarChart3 size={18} />}
            label="Статистика"
            onClick={() => setRoute("dashboard")}
          />
        </nav>
        <div className="p-4 border-t border-ink-800 text-[11px] text-mute/60">
          Прототип · демо-данные<br />amoCRM + МойСклад
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 w-full overflow-x-hidden">
        <header className="sticky top-0 z-30 border-b border-ink-800 bg-ink-950/95 backdrop-blur pt-[env(safe-area-inset-top)]">
          {/* Мобильная шапка: лого по центру, магазин + пользователь — отдельной строкой */}
          <div className="md:hidden px-4 pb-3">
            <div className="flex justify-center py-2.5">
              <img src={logoFull} alt="MANSBAND" className="h-7 w-auto max-w-[min(100%,200px)] select-none" />
            </div>
            <div className="flex items-center gap-2 min-w-0">
              <Selector
                compact
                icon={<StoreIcon size={14} />}
                value={activeStore}
                options={STORES.filter((s) => s !== "Онлайн-магазин")}
                onChange={(v) => setActiveStore(v as typeof activeStore)}
              />
              <UserMenu
                compact
                active={activeConsultant}
                consultants={CONSULTANTS.filter(
                  (c) => c.role === "consultant" || c.role === "callmanager"
                ).map((c) => c.name)}
                onPick={setActiveConsultant}
                onNavigate={setRoute}
                pendingQueue={pendingQueue}
                activeRoute={route}
              />
            </div>
          </div>
          {/* Десктопная шапка */}
          <div className="hidden md:flex px-8 py-3.5 items-center gap-3">
            <div className="flex-1" />
            <Selector
              icon={<StoreIcon size={15} />}
              value={activeStore}
              options={STORES.filter((s) => s !== "Онлайн-магазин")}
              onChange={(v) => setActiveStore(v as typeof activeStore)}
            />
            <UserMenu
              active={activeConsultant}
              consultants={CONSULTANTS.filter(
                (c) => c.role === "consultant" || c.role === "callmanager"
              ).map((c) => c.name)}
              onPick={setActiveConsultant}
              onNavigate={setRoute}
              pendingQueue={pendingQueue}
              activeRoute={route}
            />
          </div>
        </header>
        <main className="flex-1 px-4 md:px-8 py-6 pb-24 md:pb-6 overflow-x-hidden max-w-full">{children}</main>
        {/* Mobile bottom nav — 4 крупные кнопки для удобного тапа */}
        <nav className="md:hidden sticky bottom-0 z-20 border-t border-ink-800 bg-ink-900/95 backdrop-blur grid grid-cols-4 pb-[env(safe-area-inset-bottom)]">
          {MOBILE_NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => setRoute(n.id)}
              className={`flex flex-col items-center justify-center gap-1.5 py-3 min-h-[64px] text-[11px] font-medium transition active:bg-ink-800 ${
                route === n.id ? "text-gold" : "text-mute"
              }`}
            >
              {n.icon}
              <span className="leading-none">{n.label}</span>
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}

function NavButton({
  active,
  icon,
  label,
  onClick,
  badge,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
  badge?: number;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-[14px] font-medium transition ${
        active ? "bg-gold/15 text-gold-soft" : "text-mute hover:bg-ink-800 hover:text-white"
      }`}
    >
      <span className={active ? "text-gold" : ""}>{icon}</span>
      <span className="flex-1 text-left">{label}</span>
      {badge !== undefined && badge > 0 && (
        <span className="chip bg-white text-ink-950 font-semibold px-2 py-0.5">{badge}</span>
      )}
    </button>
  );
}

function NavGroup({
  icon,
  label,
  items,
  activeGroup,
  onPick,
  defaultOpen = false,
}: {
  icon: ReactNode;
  label: string;
  items: { group: string; label: string; status: string }[];
  activeGroup: string | null;
  onPick: (group: string, status: string) => void;
  defaultOpen?: boolean;
}) {
  const childActive = items.some((i) => i.group === activeGroup);
  const [open, setOpen] = useState(defaultOpen || childActive);

  useEffect(() => {
    if (childActive) setOpen(true);
  }, [childActive]);

  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-[14px] font-medium transition ${
          childActive ? "text-gold-soft" : "text-mute hover:bg-ink-800 hover:text-white"
        }`}
      >
        <span className={childActive ? "text-gold" : ""}>{icon}</span>
        <span className="flex-1 text-left">{label}</span>
        {open ? <ChevronDown size={15} className="text-mute" /> : <ChevronRight size={15} className="text-mute" />}
      </button>
      {open && (
        <div className="mt-0.5 ml-4 pl-3 border-l border-ink-700 space-y-0.5">
          {items.map((i) => (
            <button
              key={i.group}
              onClick={() => onPick(i.group, i.status)}
              className={`w-full text-left px-3 py-2 rounded-lg text-[13px] font-medium transition ${
                i.group === activeGroup
                  ? "bg-gold/15 text-gold-soft"
                  : "text-mute hover:bg-ink-800 hover:text-white"
              }`}
            >
              {i.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Selector({
  icon,
  value,
  options,
  onChange,
  compact = false,
}: {
  icon: ReactNode;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  compact?: boolean;
}) {
  return (
    <div
      className={`relative flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 hover:border-gold/40 transition min-w-0 ${
        compact ? "flex-1 pl-2.5 pr-7 py-2" : "inline-flex gap-2 pl-3 pr-2 py-2"
      }`}
    >
      <span className="text-mute shrink-0">{icon}</span>
      <select
        className={`bg-transparent text-white font-medium focus:outline-none appearance-none cursor-pointer min-w-0 ${
          compact ? "flex-1 text-[12px] truncate pr-1" : "text-[13px] pr-4"
        }`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o} value={o} className="bg-ink-850">
            {o}
          </option>
        ))}
      </select>
      <ChevronDown
        size={compact ? 13 : 14}
        className="text-mute absolute right-2 pointer-events-none shrink-0"
      />
    </div>
  );
}

function UserMenu({
  active,
  consultants,
  onPick,
  onNavigate,
  pendingQueue,
  activeRoute,
  compact = false,
}: {
  active: string;
  consultants: string[];
  onPick: (name: string) => void;
  onNavigate: (r: Route) => void;
  pendingQueue: number;
  activeRoute: Route;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [showUsers, setShowUsers] = useState(false);

  function close() {
    setOpen(false);
    setShowUsers(false);
  }

  const itemCls =
    "w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-[14px] text-mute-soft hover:bg-ink-800 hover:text-white transition text-left";

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`inline-flex items-center rounded-lg border border-ink-700 bg-ink-900 hover:border-gold/40 transition ${
          compact ? "gap-1.5 pl-2 pr-2 py-2" : "gap-2 pl-2 pr-2.5 py-2"
        }`}
        aria-label={compact ? `Пользователь: ${active}` : undefined}
      >
        <span
          className={`rounded-full bg-gold/20 text-gold-soft grid place-items-center font-bold shrink-0 ${
            compact ? "w-7 h-7 text-[12px]" : "w-6 h-6 text-[11px]"
          }`}
        >
          {active[0]}
        </span>
        {!compact && (
          <>
            <span className="text-[13px] text-white font-medium max-w-[120px] truncate">{active}</span>
            <ChevronDown size={14} className="text-mute shrink-0" />
          </>
        )}
        {compact && <ChevronDown size={13} className="text-mute shrink-0" />}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={close} />
          <div
            className={`absolute right-0 top-full mt-2 z-50 card p-1.5 ${
              compact ? "w-[min(calc(100vw-2rem),16rem)]" : "w-64"
            }`}
          >
            {!showUsers ? (
              <>
                <div className="flex items-center gap-2.5 px-3 py-2.5">
                  <span className="w-9 h-9 rounded-full bg-gold/20 text-gold-soft grid place-items-center text-sm font-bold">
                    {active[0]}
                  </span>
                  <div className="min-w-0">
                    <div className="text-white font-semibold truncate">{active}</div>
                    <div className="text-[11px] text-mute">Текущий пользователь</div>
                  </div>
                </div>
                <div className="h-px bg-ink-700 my-1.5" />
                <button
                  className={`${itemCls} ${activeRoute === "queue" ? "bg-ink-800 text-white" : ""}`}
                  onClick={() => {
                    onNavigate("queue");
                    close();
                  }}
                >
                  <Send size={17} />
                  <span className="flex-1">Очередь Эдвина</span>
                  {pendingQueue > 0 && (
                    <span className="chip bg-white text-ink-950 font-semibold px-2 py-0.5">{pendingQueue}</span>
                  )}
                </button>
                <button
                  className={`${itemCls} ${activeRoute === "dashboard" ? "bg-ink-800 text-white" : ""}`}
                  onClick={() => {
                    onNavigate("dashboard");
                    close();
                  }}
                >
                  <BarChart3 size={17} />
                  <span className="flex-1">Статистика</span>
                </button>
                <div className="h-px bg-ink-700 my-1.5" />
                <button className={itemCls} onClick={() => setShowUsers(true)}>
                  <Users size={17} />
                  <span className="flex-1">Сменить пользователя</span>
                  <ChevronDown size={14} className="-rotate-90 text-mute" />
                </button>
              </>
            ) : (
              <>
                <button
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[13px] text-mute hover:text-white hover:bg-ink-800 transition"
                  onClick={() => setShowUsers(false)}
                >
                  <ArrowLeft size={15} /> Назад
                </button>
                <div className="field-label px-3 pt-2 pb-1">Консультанты</div>
                <div className="max-h-64 overflow-y-auto">
                  {consultants.map((name) => (
                    <button
                      key={name}
                      className={`${itemCls} ${name === active ? "text-white" : ""}`}
                      onClick={() => {
                        onPick(name);
                        close();
                      }}
                    >
                      <span className="w-7 h-7 rounded-full bg-ink-700 text-mute-soft grid place-items-center text-[11px] font-bold">
                        {name[0]}
                      </span>
                      <span className="flex-1">{name}</span>
                      {name === active && <Check size={16} className="text-white" />}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
