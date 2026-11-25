export type RevealBox = { top: number; bottom: number };

export function revealDelta(input: {
  zone: RevealBox;
  keep: RevealBox;
  show?: RevealBox | null;
  companion?: RevealBox | null;
  minDelta?: number;
  showPeek?: number;
}): { delta: number; companionKept: boolean };
