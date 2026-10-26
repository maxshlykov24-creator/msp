import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  PanelLeft,
  PlusCircle,
  LayoutList,
  Boxes,
  ClipboardList,
  Ticket,
  Building2,
  Send,
  Wallet,
  BarChart3,
  Store as StoreIcon,
  ChevronDown,
  LogOut,
  History,
  UserCog,
  SlidersHorizontal,
} from "lucide-react";
import { useStore } from "../store";
import { useAuth } from "../auth/AuthContext";
import { canSeeFinanceQueues } from "../auth/roles";
import { CONSULTANTS, STORES } from "../data/mock";
import { OfflineBanner } from "./OfflineBanner";
import { Select, opts } from "./Select";
import { HintsToggle } from "../lib/hints";
import { ThemeToggle } from "../lib/theme";
import { BrandLogo } from "./BrandLogo";

export type Route =
  | "board"
  | "new"
  | "certificates"
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

const ICO = { size: 22 } as const;
const RAIL_KEY = "kassa.rail";

// Нижнее меню на мобильном: те же значки, что в колонке, плюс короткая подпись.
// «Эдвин» добавляется только финансам: консультанту денежные очереди не показываем.
const MOBILE_NAV_BASE: { id: Route; label: string; icon: ReactNode; badge?: "queue" }[] = [
  { id: "new", label: "Новая", icon: <PlusCircle {...ICO} /> },
  { id: "board", label: "Заявки", icon: <LayoutList {...ICO} /> },
  { id: "products", label: "Товар", icon: <Boxes {...ICO} /> },
  { id: "tasks", label: "Задачи", icon: <ClipboardList {...ICO} /> },
];

const MOBILE_NAV_FINANCE: { id: Route; label: string; icon: ReactNode; badge?: "queue" } = {
  id: "queue",
  label: "Эдвин",
  icon: <Send {...ICO} />,
  badge: "queue",
};

type RailItem = {
  id: string;
  label: string;
  icon: ReactNode;
  active: boolean;
  onClick: () => void;
  badge?: number;
  create?: boolean;
  open?: boolean;
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
  const [railOpen, setRailOpen] = useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) === "1";
    } catch {
      return false;
    }
  });
  const tipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(RAIL_KEY, railOpen ? "1" : "0");
    } catch {
      /* приватный режим */
    }
  }, [railOpen]);

  function showTip(el: HTMLElement) {
    const tip = tipRef.current;
    if (!tip) return;
    const label = el.dataset.label;
    if (!label || el.getAttribute("aria-expanded") === "true") {
      tip.hidden = true;
      return;
    }
    const rect = el.getBoundingClientRect();
    tip.textContent = label;
    tip.style.top = `${rect.top + rect.height / 2}px`;
    tip.style.left = `${rect.right + 10}px`;
    tip.hidden = false;
  }

  function hideTip() {
    if (tipRef.current) tipRef.current.hidden = true;
  }

  const work: RailItem[] = [
    {
      id: "new",
      label: "Новая заявка",
      icon: <PlusCircle {...ICO} />,
      create: true,
      active: route === "new",
      onClick: () => setRoute("new"),
    },
    {
      id: "board",
      label: "Заявки",
      icon: <LayoutList {...ICO} />,
      active: route === "board",
      onClick: () => onBoard("all", "all"),
    },
    {
      id: "products",
      label: "Товар",
      icon: <Boxes {...ICO} />,
      active: route === "products" || route === "suits",
      onClick: () => setRoute("products"),
    },
    {
      id: "tasks",
      label: "Задачи",
      icon: <ClipboardList {...ICO} />,
      active: route === "tasks",
      onClick: () => setRoute("tasks"),
    },
    {
      id: "certificates",
      label: "Сертификаты",
      icon: <Ticket {...ICO} />,
      active: route === "certificates",
      onClick: () => setRoute("certificates"),
    },
  ];

  const backoffice: RailItem[] = [
    ...(canSeeMoney
      ? [
          {
            id: "misha" as const,
            label: "Очередь Миши",
            icon: <Building2 {...ICO} />,
            active: route === "misha",
            badge: pendingCompany,
            onClick: () => setRoute("misha"),
          },
          {
            id: "queue" as const,
            label: "Очередь Эдвина",
            icon: <Send {...ICO} />,
            active: route === "queue",
            badge: pendingQueue,
            onClick: () => setRoute("queue"),
          },
        ]
      : []),
    {
      id: "shift",
      label: "Закрытие смены",
      icon: <Wallet {...ICO} />,
      active: route === "shift",
      onClick: () => setRoute("shift"),
    },
  ];

  return (
    <div className="min-h-screen flex flex-col w-full overflow-x-hidden">
      <OfflineBanner />
      <div className="flex flex-1 min-h-0 w-full">
      <aside className="hidden md:flex shrink-0 sticky top-0 h-dvh p-3 z-20">
        <nav
          className={`kassa-rail ${railOpen ? "is-open" : ""}`}
          aria-label="Разделы кассы"
          onMouseOver={(e) => {
            const btn = (e.target as HTMLElement).closest<HTMLElement>("[data-label]");
            if (btn) showTip(btn);
          }}
          onMouseLeave={hideTip}
          onFocus={(e) => {
            const btn = (e.target as HTMLElement).closest<HTMLElement>("[data-label]");
            if (btn) showTip(btn);
          }}
          onBlur={hideTip}
        >
          <button
            type="button"
            className="rail-btn rail-toggle"
            aria-expanded={railOpen}
            aria-label={railOpen ? "Свернуть меню" : "Развернуть меню"}
            data-label={railOpen ? undefined : "Развернуть меню"}
            onClick={() => {
              hideTip();
              setRailOpen((v) => !v);
            }}
          >
            <PanelLeft size={20} />
          </button>
          <div className="rail-stack">
            {work.map((item) => (
              <RailButton key={item.id} {...item} open={railOpen} />
            ))}
          </div>
          <div className="rail-sep" aria-hidden />
          <div className="rail-stack">
            {backoffice.map((item) => (
              <RailButton key={item.id} {...item} open={railOpen} />
            ))}
          </div>
        </nav>
        <div ref={tipRef} className="rail-tip" role="tooltip" hidden />
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 w-full overflow-x-hidden">
        <header className="kassa-chrome sticky top-0 z-30 border-b pt-[env(safe-area-inset-top)]">
          {/* Мобильная шапка: лого по центру, магазин + пользователь — отдельной строкой */}
          <div className="md:hidden px-4 pb-3">
            <div className="grid grid-cols-[1fr_auto_1fr] items-center py-2.5 gap-2">
              <div />
              <button type="button" onClick={onHome} aria-label="На главную">
                <BrandLogo className="h-7" />
              </button>
              <div className="justify-self-end flex flex-col items-end gap-1">
                <ThemeToggle compact />
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
          <div className="hidden md:flex flex-wrap px-4 lg:px-8 py-3.5 items-center gap-2 lg:gap-3">
            <button type="button" onClick={onHome} aria-label="На главную" className="shrink-0">
              <BrandLogo className="h-8" />
            </button>
            <div className="flex-1" />
            <ThemeToggle />
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
          <div key={route} className="page-in">
            {children}
          </div>
        </main>
        {/* Mobile bottom nav — скрыт на «Новой заявке», чтобы не перекрывать sticky-бар */}
        {showMobileNav && (
          <nav
            className={`kassa-chrome md:hidden sticky bottom-0 z-20 border-t grid pb-[env(safe-area-inset-bottom)] ${
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

function RailButton({ active, icon, label, onClick, badge, create, open }: RailItem) {
  const count = badge !== undefined && badge > 0 ? (badge > 99 ? "99+" : badge) : null;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      data-label={open ? undefined : label}
      className={`rail-btn ${active ? "is-on" : ""} ${create ? "is-create" : ""}`}
    >
      {icon}
      {open && <span className="rail-label">{label}</span>}
      {count !== null && <span className="rail-badge">{count}</span>}
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
    <Select
      value={value}
      onChange={onChange}
      options={opts(...options)}
      leading={icon}
      size={compact ? "sm" : "md"}
      className={compact ? "flex-1 min-w-0" : "w-[168px] xl:w-[220px]"}
    />
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
            className={`absolute right-0 top-full mt-2 z-50 card p-1.5 kassa-pop ${
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
              <span className="flex-1">История изменений</span>
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
