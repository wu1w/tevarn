/**
 * 运行：cd frontend && npx tsx lib/colResize.selftest.ts
 */
import {
  clampColWidth,
  shouldSnapCollapse,
  widthFromDrag,
} from './colResize';

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

assert(clampColWidth(100, 200, 420) === 200, 'clamp min');
assert(clampColWidth(900, 200, 420) === 420, 'clamp max');
assert(clampColWidth(260, 200, 420) === 260, 'clamp mid');

assert(widthFromDrag({ startX: 300, startW: 260, clientX: 340, edge: 'right' }) === 300, 'right grow');
assert(widthFromDrag({ startX: 800, startW: 400, clientX: 760, edge: 'left' }) === 440, 'left grow');

assert(shouldSnapCollapse(150, 200), 'snap below min-slack');
assert(!shouldSnapCollapse(190, 200), 'no snap near min');

console.log('colResize.selftest OK');
