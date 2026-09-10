import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  LayoutList,
  PlusCircle,
  Send,
  BarChart3,
  Wallet,
  Ticket,
  Store as StoreIcon,
  ChevronDown,
  LogOut,
  Boxes,
  ClipboardList,
  History,
  Building2,
  UserCog,
  Gift,
  SlidersHorizontal,
} from "lucide-react";
import { useStore } from "../store";
import { useAuth } from "../auth/AuthContext";
import { canSeeFinanceQueues } from "../auth/roles";
import { CONSULTANTS, STORES } from "../data/mock";
import { OfflineBanner } from "./OfflineBanner";
import { HintsToggle } from "../lib/hints";
import logoFull from "../assets/logo-full.png";

export type Route =
  | "board"
  | "new"
  | "certificates"
  | "sary"
  | "queue"
  | "misha"
  | "shift"
  | "dashboard"
  | "products"
  | "suits"
  | "tasks"
  | "history"
  | "roles"
  | "settings"
  | "audit"; // старый hash → история

// Нижнее меню на мобильном: товар и очереди — на виду, не в бургер-меню.
// «Эдвин» добавляется только финансам: консультанту денежные очереди не показываем.
const MOBILE_NAV_BASE: { id: Route; label: string; icon: ReactNode; badge?: "queue" }[] = [
  { id: "new", label: "Новая", icon: <PlusCircle size={22} /> },
  { id: "board", label: "Заявки", icon: <LayoutList size={22} /> },
  { id: "products", label: "Товар", icon: <Boxes size={22} /> },
  { id: "tasks", label: "Очереди", icon: <ClipboardList size={22} /> },
];

const MOBILE_NAV_FINANCE: { id: Route; label: string; icon: ReactNode; badge?: "queue" } = {
  id: "queue",
  label: "Эдвин",
  icon: <Send size={22} />,
  badge: "queue",
};

export function Layout({
  route,
  setRoute,
  onBoard,
  onHome,
  children,
}: {
  route: Route;
  setRoute: (r: Route) => void;
  onBoard: (group: string, status: string) => void;
  /** Клик по логотипу — на главную (все заявки). */
  onHome: () => void;
  children: ReactNode;
}) {
  const { activeStore, setActiveStore, setActiveConsultant, queue } = useStore();
  const { user, logout } = useAuth();
  const pendingQueue = queue.filter((q) => q.status === "pending").length;
  // Очередь Миши — документы и ателье; консультанту она не нужна.
  const pendingCompany = queue.filter(
    (q) => q.status === "pending" && (q.kind === "documents" || q.kind === "atelier")
  ).length;
  const canSeeMoney = canSeeFinanceQueues(user?.role, user?.login, user?.name);
  const mobileNav = canSeeMoney ? [...MOBILE_NAV_BASE, MOBILE_NAV_FINANCE] : MOBILE_NAV_BASE;
  // На экране новой заявки нижнее меню скрываем — место под sticky-блок суммы/кнопок.
  const showMobileNav = route !== "new";

  // Дефолт консультанта в формах = имя залогиненного, если он в справочнике.
  useEffect(() => {
    if (!user?.name) return;
    const names = CONSULTANTS.filter(
      (c) => c.role === "consultant" || c.role === "callmanager"
    ).map((c) => c.name);
    if (names.includes(user.name)) setActiveConsultant(user.name);
  }, [user?.name, setActiveConsultant]);

  const displayName = user?.name ?? "Пользователь";

  return (
    <div className="min-h-screen flex flex-col w-full overflow-x-hidden">
      <OfflineBanner />
      <div className="flex flex-1 min-h-0 w-full">
      {/* Sidebar */}
      <aside className="hidden md:flex w-64 shrink-0 flex-col border-r border-ink-800 bg-ink-900/60 backdrop-blur sticky top-0 h-screen">
        <button
          type="button"
          onClick={onHome}
          aria-label="На главную"
          className="px-5 py-6 border-b border-ink-800 text-left w-full hover:bg-ink-800/40 transition"
        >
          <img src={logoFull} alt="MANSBAND" className="h-14 w-auto select-none" />
          <div className="text-[11px] text-mute tracking-[0.28em] uppercase mt-2.5 pl-0.5">Касса</div>
        </button>
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          <NavButton
            active={route === "new"}
            icon={<PlusCircle size={18} />}
            label="Новая заявка"
            onClick={() => setRoute("new")}
          />
          <NavButton
            active={route === "board"}
            icon={<LayoutList size={18} />}
            label="Все заявки"
            onClick={() => onBoard("all", "all")}
          />
          <NavButton
            active={route === "products" || route === "suits"}
            icon={<Boxes size={18} />}
            label="Поиск товара"
            onClick={() => setRoute("products")}
          />
          <NavButton
            active={route === "tasks"}
            icon={<ClipboardList size={18} />}
            label="Задачи"
            onClick={() => setRoute("tasks")}
          />
          <NavButton
            active={route === "certificates"}
            icon={<Ticket size={18} />}
            label="Реестр сертификатов"
            onClick={() => setRoute("certificates")}
          />
          <NavButton
            active={route === "sary"}
            icon={<Gift size={18} />}
            label="Ведомость САР"
            onClick={() => setRoute("sary")}
          />
          {canSeeMoney && (
            <>
              <NavButton
                active={route === "misha"}
                icon={<Building2 size={18} />}
                label="Очередь Миши"
                onClick={() => setRoute("misha")}
                badge={pendingCompany}
              />
              <NavButton
                active={route === "queue"}
                icon={<Send size={18} />}
                label="Очередь Эдвина"
                onClick={() => setRoute("queue")}
                badge={pendingQueue}
              />
            </>
          )}

          <NavButton
            active={route === "shift"}
            icon={<Wallet size={18} />}
            label="Закрытие смены"
            onClick={() => setRoute("shift")}
          />
          <NavButton
            active={route === "history" || route === "audit"}
            icon={<History size={18} />}
            label="История изменений"
            onClick={() => setRoute("history")}
          />
        </nav>
        <div className="p-4 border-t border-ink-800 text-[11px] text-mute/60 tracking-[0.18em] uppercase">
          MANSBAND
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 w-full overflow-x-hidden">
        <header className="sticky top-0 z-30 border-b border-ink-800 bg-ink-950 md:bg-ink-950/95 md:backdrop-blur pt-[env(safe-area-inset-top)]">
          {/* Мобильная шапка: лого по центру, магазин + пользователь — отдельной строкой */}
          <div className="md:hidden px-4 pb-3">
            <div className="grid grid-cols-[1fr_auto_1fr] items-center py-2.5 gap-2">
              <div />
              <button type="button" onClick={onHome} aria-label="На главную">
                <img src={logoFull} alt="MANSBAND" className="h-7 w-auto max-w-[140px] select-none" />
              </button>
              <div className="justify-self-end">
                <HintsToggle compact />
              </div>
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
                displayName={displayName}
                onLogout={logout}
                onNavigate={setRoute}
                activeRoute={route}
              />
            </div>
          </div>
          {/* Десктопная шапка */}
          <div className="hidden md:flex px-8 py-3.5 items-center gap-3">
            <div className="flex-1" />
            <HintsToggle />
            <Selector
              icon={<StoreIcon size={15} />}
              value={activeStore}
              options={STORES.filter((s) => s !== "Онлайн-магазин")}
              onChange={(v) => setActiveStore(v as typeof activeStore)}
            />
            <UserMenu
              displayName={displayName}
              onLogout={logout}
              onNavigate={setRoute}
              activeRoute={route}
            />
          </div>
        </header>
        <main
          className={`flex-1 px-4 md:px-8 py-6 overflow-x-hidden max-w-full ${
            showMobileNav ? "pb-24 md:pb-6" : "pb-6"
          }`}
        >
          {children}
        </main>
        {/* Mobile bottom nav — скрыт на «Новой заявке», чтобы не перекрывать sticky-бар */}
        {showMobileNav && (
          <nav
            className={`md:hidden sticky bottom-0 z-20 border-t border-ink-800 bg-ink-900 grid pb-[env(safe-area-inset-bottom)] ${
              mobileNav.length === 5 ? "grid-cols-5" : "grid-cols-4"
            }`}
          >
            {mobileNav.map((n) => {
              const badge = n.badge === "queue" ? pendingQueue : 0;
              return (
                <button
                  key={n.id}
                  onClick={() => (n.id === "board" ? onBoard("all", "all") : setRoute(n.id))}
                  className={`relative flex flex-col items-center justify-center gap-1 py-2.5 min-h-[64px] text-[10px] font-medium transition active:bg-ink-800 ${
                    route === n.id ? "text-gold" : "text-mute"
                  }`}
                >
                  {n.icon}
                  <span className="leading-none">{n.label}</span>
                  {badge > 0 && (
                    <span className="absolute top-1.5 right-[18%] min-w-[16px] h-4 px-1 rounded-full bg-white text-ink-950 text-[10px] font-bold grid place-items-center">
                      {badge > 99 ? "99+" : badge}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        )}
      </div>
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
  displayName,
  onLogout,
  onNavigate,
  activeRoute,
  compact = false,
}: {
  displayName: string;
  onLogout: () => void;
  onNavigate: (r: Route) => void;
  activeRoute: Route;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { user } = useAuth();
  const canSettings = user?.role === "rop" || user?.role === "admin";

  function close() {
    setOpen(false);
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
        aria-label={compact ? `Пользователь: ${displayName}` : undefined}
      >
        <span
          className={`rounded-full bg-gold/20 text-gold-soft grid place-items-center font-bold shrink-0 ${
            compact ? "w-7 h-7 text-[12px]" : "w-6 h-6 text-[11px]"
          }`}
        >
          {displayName[0]}
        </span>
        {!compact && (
          <>
            <span className="text-[13px] text-white font-medium max-w-[120px] truncate">{displayName}</span>
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
              compact ? "w-[min(calc(100%-2rem),16rem)]" : "w-64"
            }`}
          >
            <div className="flex items-center gap-2.5 px-3 py-2.5">
              <span className="w-9 h-9 rounded-full bg-gold/20 text-gold-soft grid place-items-center text-sm font-bold">
                {displayName[0]}
              </span>
              <div className="min-w-0">
                <div className="text-white font-semibold truncate">{displayName}</div>
                <div className="text-[11px] text-mute">Вход по логину</div>
              </div>
            </div>
            <div className="h-px bg-ink-700 my-1.5" />
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
            <button
              className={`${itemCls} ${activeRoute === "history" || activeRoute === "audit" ? "bg-ink-800 text-white" : ""}`}
              onClick={() => {
                onNavigate("history");
                close();
              }}
            >
              <History size={17} />
              <span className="flex-1">История</span>
            </button>
            <button
              className={`${itemCls} ${activeRoute === "roles" ? "bg-ink-800 text-white" : ""}`}
              onClick={() => {
                onNavigate("roles");
                close();
              }}
            >
              <UserCog size={17} />
              <span className="flex-1">Роли</span>
            </button>
            {canSettings && (
              <button
                className={`${itemCls} ${activeRoute === "settings" ? "bg-ink-800 text-white" : ""}`}
                onClick={() => {
                  onNavigate("settings");
                  close();
                }}
              >
                <SlidersHorizontal size={17} />
                <span className="flex-1">Настройки мотивации</span>
              </button>
            )}
            <div className="h-px bg-ink-700 my-1.5" />
            <button
              className={itemCls}
              onClick={() => {
                close();
                onLogout();
              }}
            >
              <LogOut size={17} />
              <span className="flex-1">Выйти</span>
            </button>
          </div>
        </>
      )}
    </div>
  );
}
