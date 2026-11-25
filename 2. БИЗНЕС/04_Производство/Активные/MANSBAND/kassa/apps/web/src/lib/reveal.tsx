import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import { revealDelta } from "./revealDelta.mjs";

export type RevealRequest = {
  /** То, что только что внесли. Остаётся целиком над панелью. */
  keep: HTMLElement;
  /** Начало нового куска снизу. Берём верх, не весь низ. */
  show?: HTMLElement | null;
  /** Поиск или поля следующей оплаты, если помещаются рядом. */
  companion?: HTMLElement | null;
  /** Поле поиска: снимаем фокус, только если оно не помещается со строкой. */
  companionInput?: HTMLElement | null;
  /** Фокус не перебивает завершённое добавление строки в том же кадре. */
  priority?: "focus" | "commit";
  /** Повтор замера, если список поверх формы закрывается в соседнем кадре. */
  waits?: number;
};

type RevealApi = {
  reveal: (request: RevealRequest) => void;
  userScrolledAt: () => number;
};

const RevealContext = createContext<RevealApi | null>(null);

export function useReveal(): RevealApi | null {
  return useContext(RevealContext);
}

type Runtime = {
  gen: number;
  userScrollAt: number;
  raf: number;
  pending: RevealRequest | null;
  last: RevealRequest | null;
  lastAt: number;
  measuring: boolean;
  retried: boolean;
};

function boxOf(el: HTMLElement | null | undefined): { top: number; bottom: number } | null {
  if (!el?.isConnected) return null;
  const rect = el.getBoundingClientRect();
  if (rect.height <= 0 && rect.width <= 0) return null;
  return { top: rect.top, bottom: rect.bottom };
}

function scrollable(el: HTMLElement): boolean {
  const overflow = getComputedStyle(el).overflowY;
  return (overflow === "auto" || overflow === "scroll" || overflow === "overlay") && el.scrollHeight > el.clientHeight + 2;
}

export function scrollTarget(root: HTMLElement): HTMLElement {
  const modal = root.closest(".kassa-modal-body");
  if (modal instanceof HTMLElement) return modal;
  let node: HTMLElement | null = root.parentElement;
  while (node) {
    if (scrollable(node)) return node;
    node = node.parentElement;
  }
  return (document.scrollingElement as HTMLElement | null) ?? document.documentElement;
}

function isDocumentScroller(el: HTMLElement): boolean {
  return el === document.scrollingElement || el === document.documentElement || el === document.body;
}

function readTop(el: HTMLElement): number {
  return isDocumentScroller(el) ? window.scrollY : el.scrollTop;
}

function writeTop(el: HTMLElement, top: number) {
  if (isDocumentScroller(el)) window.scrollTo(0, top);
  else el.scrollTop = top;
}

function visibleZone(root: HTMLElement): { top: number; bottom: number } {
  const view = window.visualViewport;
  const viewTop = view?.offsetTop ?? 0;
  const viewBottom = viewTop + (view?.height ?? window.innerHeight);
  const dock = root.querySelector<HTMLElement>(".kassa-action-dock");
  const dockTop = dock?.getBoundingClientRect().top;
  const limit = dockTop == null || dockTop <= viewTop + 40 ? viewBottom : Math.min(viewBottom, dockTop);
  return { top: viewTop + 12, bottom: limit - 16 };
}

/** Список поверх страницы или чужое окно. Окно, в котором лежит сама форма, не мешает. */
export function overlayBlocks(root: HTMLElement): boolean {
  if (document.querySelector(".kassa-select-menu, .date-picker-panel, [data-reveal-popover]")) return true;
  const overlays = document.querySelectorAll(".kassa-ov.on");
  for (let i = 0; i < overlays.length; i++) {
    const ov = overlays[i];
    if (ov && !ov.contains(root)) return true;
  }
  return false;
}

function isInnerScroll(target: EventTarget | null, scroller: HTMLElement): boolean {
  if (!(target instanceof Node)) return false;
  let node: Node | null = target;
  while (node && node !== scroller) {
    if (node instanceof HTMLElement) {
      const overflow = getComputedStyle(node).overflowY;
      if (
        (overflow === "auto" || overflow === "scroll" || overflow === "overlay") &&
        node.scrollHeight > node.clientHeight + 2
      ) {
        return true;
      }
    }
    node = node.parentNode;
  }
  return false;
}

function nextBlock(from: HTMLElement, root: HTMLElement): HTMLElement | null {
  const edge = from.getBoundingClientRect().bottom;
  const blocks = root.querySelectorAll<HTMLElement>("[data-reveal-block]");
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    if (!block || block.contains(from)) continue;
    if (block.getBoundingClientRect().top >= edge - 8) return block;
  }
  return null;
}

function focusField(target: HTMLElement): HTMLElement | null {
  if (target.closest(".kassa-action-dock, [data-reveal-popover], .kassa-select-menu, .date-picker-panel")) return null;
  const el = target.closest("input, textarea, button");
  if (!(el instanceof HTMLElement)) return null;
  if (el instanceof HTMLButtonElement && !el.classList.contains("input") && !el.classList.contains("date-picker-trigger")) {
    return null;
  }
  return el;
}

function animate(scroller: HTMLElement, delta: number, runtime: Runtime) {
  const gen = ++runtime.gen;
  const start = readTop(scroller);
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    writeTop(scroller, start + delta);
    return gen;
  }
  const started = performance.now();
  const step = (now: number) => {
    if (runtime.gen !== gen) return;
    const progress = Math.min(1, (now - started) / 320);
    const eased = 1 - (1 - progress) ** 3;
    writeTop(scroller, start + delta * eased);
    if (progress < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  return gen;
}

function enqueue(runtime: Runtime, request: RevealRequest, getRoot: () => HTMLElement | null) {
  const prev = runtime.pending;
  if ((request.priority ?? "commit") !== "commit" && prev?.priority === "commit") return;
  if (!request.waits) runtime.retried = false;
  runtime.pending = request;
  if (runtime.raf) return;
  runtime.raf = requestAnimationFrame(() => {
    runtime.raf = 0;
    const pending = runtime.pending;
    runtime.pending = null;
    const root = getRoot();
    if (!pending || !root) return;
    run(root, pending, runtime, getRoot);
  });
}

function run(root: HTMLElement, request: RevealRequest, runtime: Runtime, getRoot: () => HTMLElement | null) {
  if (!request.keep.isConnected) return;
  if (overlayBlocks(root)) {
    const waits = (request.waits ?? 0) + 1;
    if (waits > 8) return;
    enqueue(runtime, { ...request, waits }, getRoot);
    return;
  }
  const keep = boxOf(request.keep);
  if (!keep) return;
  const show = boxOf(request.show);
  const companion = boxOf(request.companion);
  const { delta, companionKept } = revealDelta({
    zone: visibleZone(root),
    keep,
    show,
    companion,
  });
  runtime.last = request;
  runtime.lastAt = Date.now();
  const input = request.companionInput;
  if (delta !== 0 && input?.isConnected && document.activeElement === input) input.blur();
  if (delta === 0) return;
  animate(scrollTarget(root), delta, runtime);
}

export function RevealScope({
  rootRef,
  children,
}: {
  rootRef: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const runtime = useRef<Runtime>({
    gen: 0,
    userScrollAt: 0,
    raf: 0,
    pending: null,
    last: null,
    lastAt: 0,
    measuring: false,
    retried: false,
  });
  const scheduleRef = useRef<(request: RevealRequest) => void>(() => {});

  const schedule = (request: RevealRequest) => {
    const runtimeRef = runtime.current;
    const next = { priority: "commit" as const, ...request };
    enqueue(runtimeRef, next, () => rootRef.current);
  };
  scheduleRef.current = schedule;

  const api = useMemo<RevealApi>(
    () => ({
      reveal: (request) => scheduleRef.current(request),
      userScrolledAt: () => runtime.current.userScrollAt,
    }),
    [],
  );

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const scroller = scrollTarget(root);
    const previous = scroller.style.scrollPaddingBottom;
    const apply = () => {
      const dock = root.querySelector(".kassa-action-dock");
      const height = dock?.getBoundingClientRect().height ?? 0;
      scroller.style.scrollPaddingBottom = height > 0 ? `${Math.ceil(height + 16)}px` : previous;
    };
    apply();
    const dock = root.querySelector(".kassa-action-dock");
    const observer = new ResizeObserver(apply);
    if (dock) observer.observe(dock);
    return () => {
      observer.disconnect();
      scroller.style.scrollPaddingBottom = previous;
    };
  }, [rootRef]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const runtimeRef = runtime.current;
    scheduleRef.current = schedule;

    const onFocus = (event: FocusEvent) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !root.contains(target)) return;
      const field = focusField(target);
      if (!field) return;
      scheduleRef.current({ keep: field, priority: "focus" });
    };

    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
      const target = event.target;
      if (!(target instanceof HTMLInputElement) || target.dataset.revealEnter !== "next") return;
      scheduleRef.current({ keep: target, show: nextBlock(target, root) });
    };

    const seen = new WeakSet<HTMLElement>();
    const mo = new MutationObserver((records) => {
      for (const record of records) {
        record.addedNodes.forEach((node) => {
          if (!(node instanceof HTMLElement)) return;
          const found: HTMLElement[] = [];
          if (node.matches("[data-reveal-mount]")) found.push(node);
          node.querySelectorAll<HTMLElement>("[data-reveal-mount]").forEach((el) => found.push(el));
          for (const el of found) {
            if (seen.has(el)) continue;
            seen.add(el);
            scheduleRef.current({ keep: el });
          }
        });
      }
    });
    mo.observe(root, { childList: true, subtree: true });

    const onUserScroll = (event: Event) => {
      const current = rootRef.current;
      if (!current) return;
      if (isInnerScroll(event.target, scrollTarget(current))) return;
      runtimeRef.userScrollAt = Date.now();
      runtimeRef.gen += 1;
    };

    const onViewport = () => {
      const last = runtimeRef.last;
      const current = rootRef.current;
      if (!last || !current || runtimeRef.measuring || runtimeRef.retried || Date.now() - runtimeRef.lastAt > 400) return;
      runtimeRef.retried = true;
      runtimeRef.measuring = true;
      try {
        run(current, last, runtimeRef, () => rootRef.current);
      } finally {
        runtimeRef.measuring = false;
      }
    };

    root.addEventListener("focusin", onFocus);
    root.addEventListener("keydown", onKey);
    window.addEventListener("wheel", onUserScroll, { passive: true });
    window.addEventListener("touchmove", onUserScroll, { passive: true });
    window.visualViewport?.addEventListener("resize", onViewport);
    return () => {
      root.removeEventListener("focusin", onFocus);
      root.removeEventListener("keydown", onKey);
      window.removeEventListener("wheel", onUserScroll);
      window.removeEventListener("touchmove", onUserScroll);
      window.visualViewport?.removeEventListener("resize", onViewport);
      mo.disconnect();
      if (runtimeRef.raf) cancelAnimationFrame(runtimeRef.raf);
      runtimeRef.raf = 0;
      scheduleRef.current = () => {};
    };
  }, [rootRef]);

  return <RevealContext.Provider value={api}>{children}</RevealContext.Provider>;
}
