import React, { useRef } from 'react'
import { armScanAudio, burstCode, pushBurst, type Burst } from './scanCue'

/** Enter или Tab сразу после быстрой пачки символов — это сканер, не ручной ввод. */
export function useScanBurst(onScan?: (code: string) => void) {
  const burst = useRef<Burst | null>(null)
  const onScanRef = useRef(onScan)
  onScanRef.current = onScan
  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    armScanAudio()
    if (event.nativeEvent.isComposing) return
    if (event.key === 'Enter' || event.key === 'NumpadEnter' || event.key === 'Tab') {
      const code = burstCode(burst.current, performance.now())
      burst.current = null
      if (!code || !onScanRef.current) return
      event.preventDefault()
      event.stopPropagation()
      onScanRef.current(code)
      return
    }
    if (event.ctrlKey || event.metaKey || event.altKey || event.key.length !== 1) return
    burst.current = pushBurst(burst.current, event.key, performance.now())
  }
  function onBeforeInput(event: React.FormEvent<HTMLInputElement>) {
    const native = event.nativeEvent
    if (!(native instanceof InputEvent)) return
    if (native.inputType === 'insertFromPaste' || native.inputType === 'insertFromDrop') {
      burst.current = null
      return
    }
    const data = native.data || ''
    if (data.length > 1) {
      const now = performance.now()
      burst.current = { chars: data, last: now, gaps: Array.from({ length: data.length - 1 }, () => 1) }
    }
  }
  return { onKeyDown, onBeforeInput }
}
