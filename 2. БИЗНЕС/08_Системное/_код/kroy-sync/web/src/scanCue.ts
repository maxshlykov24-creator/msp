/** Клавиатурный сканер: символы подряд и Enter сразу за ними. Ручной набор сюда не попадает. */
export const SCAN_MAX_GAP_MS = 70
export const SCAN_MIN_LENGTH = 4

export type Burst = { chars: string; last: number; gaps: number[] }
export type ScanHit = 'one' | 'none' | 'many' | 'meter' | 'taken'
export type ScanItem = { id?: string; unit?: string; barcodes?: unknown }

export const scanMessages = {
  one: 'Позиция добавлена',
  none: 'Штрихкод не найден',
  many: 'Штрихкод подходит нескольким позициям',
  meter: 'Метры вводятся вручную',
  taken: 'Эта позиция уже добавлена',
} as const

export function pushBurst(burst: Burst | null, key: string, now: number): Burst {
  if (!burst || now - burst.last > SCAN_MAX_GAP_MS) return { chars: key, last: now, gaps: [] }
  return { chars: burst.chars + key, last: now, gaps: [...burst.gaps, now - burst.last] }
}

export function burstCode(burst: Burst | null, now: number): string | null {
  if (!burst || now - burst.last > SCAN_MAX_GAP_MS) return null
  const code = burst.chars.replace(/[\u0000-\u001F\u007F]/g, '').trim()
  if (code.length < SCAN_MIN_LENGTH || burst.gaps.some(gap => gap > SCAN_MAX_GAP_MS)) return null
  return code
}

function codes(item: ScanItem) {
  return Array.isArray(item.barcodes) ? item.barcodes.map(barcode => String(barcode).trim()) : []
}

export function classifyBarcode(selectable: readonly ScanItem[], code: string, pool: readonly ScanItem[] = selectable): { hit: ScanHit; id?: string } {
  const needle = code.trim()
  const hits = pool.filter(item => item.id && codes(item).includes(needle))
  if (hits.length === 0) return { hit: 'none' }
  if (hits.length > 1) return { hit: 'many' }
  const item = hits[0]
  if (item.unit === 'meter') return { hit: 'meter', id: item.id }
  if (!selectable.some(row => row.id === item.id)) return { hit: 'taken', id: item.id }
  return { hit: 'one', id: item.id }
}

let audio: AudioContext | null = null
let chain = Promise.resolve()

export function armScanAudio() {
  const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctx) return null
  if (!audio) audio = new Ctx()
  if (audio.state === 'suspended') void audio.resume()
  return audio
}

function blip(ctx: AudioContext, when: number, frequency: number, duration: number, gain: number, type: OscillatorType) {
  const osc = ctx.createOscillator()
  const amp = ctx.createGain()
  osc.type = type
  osc.frequency.setValueAtTime(frequency, when)
  amp.gain.setValueAtTime(0.0001, when)
  amp.gain.exponentialRampToValueAtTime(gain, when + 0.012)
  amp.gain.exponentialRampToValueAtTime(0.0001, when + duration)
  osc.connect(amp)
  amp.connect(ctx.destination)
  osc.start(when)
  osc.stop(when + duration + 0.02)
}

function tone(kind: 'ok' | 'bad') {
  const ctx = armScanAudio()
  if (!ctx) return Promise.resolve()
  const start = ctx.currentTime + 0.01
  if (kind === 'ok') blip(ctx, start, 1318, 0.09, 0.22, 'sine')
  else {
    blip(ctx, start, 196, 0.11, 0.28, 'triangle')
    blip(ctx, start + 0.15, 146, 0.16, 0.28, 'triangle')
  }
  const wait = kind === 'ok' ? 120 : 340
  return new Promise<void>(resolve => window.setTimeout(resolve, wait))
}

/** Короткий высокий сигнал — позиция добавлена. Два низких — ошибка. Очередь, чтобы сканы не слипались. */
export function playScanCue(kind: 'ok' | 'bad') {
  chain = chain.then(() => tone(kind)).catch(() => undefined)
}
