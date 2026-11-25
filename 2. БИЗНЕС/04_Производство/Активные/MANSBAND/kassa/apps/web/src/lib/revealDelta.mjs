/**
 * Сдвиг прокрутки после завершённого ввода.
 * Положительный delta листает вниз. Прямоугольники в координатах окна.
 *
 * keep остаётся целиком в зоне. companion (поиск, поля следующей оплаты)
 * присоединяется только если связка помещается. show открывается сверху,
 * примерно на showPeek, а не до своего низа.
 */

/**
 * @param {{
 *   zone: { top: number, bottom: number },
 *   keep: { top: number, bottom: number },
 *   show?: { top: number, bottom: number } | null,
 *   companion?: { top: number, bottom: number } | null,
 *   minDelta?: number,
 *   showPeek?: number,
 * }} input
 * @returns {{ delta: number, companionKept: boolean }}
 */
export function revealDelta(input) {
  const minDelta = input.minDelta ?? 12;
  const showPeek = input.showPeek ?? 96;
  const zone = input.zone;
  const zoneHeight = zone.bottom - zone.top;
  if (!(zoneHeight > 0) || !input.keep) return { delta: 0, companionKept: false };

  let anchor = { top: input.keep.top, bottom: input.keep.bottom };
  let companionKept = false;
  const companion = input.companion;
  if (companion && Number.isFinite(companion.top) && Number.isFinite(companion.bottom)) {
    const top = Math.min(anchor.top, companion.top);
    const bottom = Math.max(anchor.bottom, companion.bottom);
    if (bottom - top <= zoneHeight + 0.5) {
      anchor = { top, bottom };
      companionKept = true;
    }
  }

  if (anchor.bottom - anchor.top > zoneHeight) {
    const delta = anchor.top - zone.top;
    return { delta: Math.abs(delta) < minDelta ? 0 : delta, companionKept: false };
  }

  // delta >= deltaMin прижимает низ к низу зоны, delta <= deltaMax не выпускает верх.
  const deltaMin = anchor.bottom - zone.bottom;
  const deltaMax = anchor.top - zone.top;

  let desired = 0;
  if (anchor.bottom > zone.bottom + 0.5) desired = anchor.bottom - zone.bottom;
  else if (anchor.top < zone.top - 0.5) desired = anchor.top - zone.top;

  const show = input.show;
  if (show && Number.isFinite(show.top)) {
    const showBottom = Number.isFinite(show.bottom) ? show.bottom : show.top + showPeek;
    const peekBottom = Math.min(showBottom, show.top + showPeek);
    let showDesired = 0;
    if (peekBottom > zone.bottom + 0.5) showDesired = peekBottom - zone.bottom;
    else if (show.top < zone.top - 0.5) showDesired = show.top - zone.top;
    if (showDesired > desired) desired = showDesired;
  }

  let delta = Math.min(Math.max(desired, deltaMin), deltaMax);
  if (Math.abs(delta) < minDelta) delta = 0;
  return { delta, companionKept };
}
