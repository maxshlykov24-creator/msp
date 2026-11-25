import assert from 'node:assert/strict'
import { burstCode, classifyBarcode, pushBurst, SCAN_MAX_GAP_MS, type Burst } from './src/scanCue.ts'

function read(text: string, gap: number, enterGap = gap) {
  let burst: Burst | null = null
  let now = 0
  for (const key of text) {
    now += gap
    burst = pushBurst(burst, key, now)
  }
  return burstCode(burst, now + enterGap)
}

const ean = '4601234567890'
assert.equal(read(ean, 12), ean)
assert.equal(read(ean, 12, SCAN_MAX_GAP_MS + 30), null)
assert.equal(read(ean, 180), null)
assert.equal(read('001', 10), null)
assert.equal(read('AB12', 15), 'AB12')

let burst: Burst | null = null
let now = 0
for (const key of 'лонг') {
  now += 160
  burst = pushBurst(burst, key, now)
}
now += 400
for (const key of ean) {
  now += 10
  burst = pushBurst(burst, key, now)
}
assert.equal(burstCode(burst, now + 10), ean)

const items = [
  { id: 'a', unit: 'piece', barcodes: ['4601234567890', '  2222  '] },
  { id: 'b', unit: 'piece', barcodes: ['4601234567890'] },
  { id: 'c', unit: 'meter', barcodes: ['5555'] },
  { id: 'd', unit: 'piece', barcodes: ['7777'] },
]
assert.deepEqual(classifyBarcode([items[3]], '7777', items), { hit: 'one', id: 'd' })
assert.equal(classifyBarcode(items, '9999').hit, 'none')
assert.equal(classifyBarcode(items, '4601234567890').hit, 'many')
assert.equal(classifyBarcode(items, '5555').hit, 'meter')
assert.equal(classifyBarcode(items.filter(item => item.id !== 'd'), '7777', items).hit, 'taken')
assert.equal(classifyBarcode(items, '2222').hit, 'one')
assert.equal(classifyBarcode([{ id: 'x', barcodes: ['46012345678901'] }], '4601234567890').hit, 'none')

console.log('scan-cue: ok')
