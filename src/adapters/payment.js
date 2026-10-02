import { getDb, cid } from '../db.js';
import { nowIso } from '../domain/time.js';

// 模拟支付适配器
// 支付以 request_id 幂等：同一回执重复发起/回调，绝不重复扣款。
export function pay({ receiptId, amountCents, requestId, db = getDb() }) {
  const existing = db.prepare('SELECT * FROM payments WHERE request_id = ?').get(requestId);
  if (existing) {
    return {
      reused: true,
      paymentId: existing.id,
      providerRef: existing.provider_ref,
      status: existing.status,
      amountCents: existing.amount_cents,
    };
  }
  const id = cid('pay_');
  const providerRef = `MOCK-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  db.prepare(`INSERT INTO payments (id, receipt_id, request_id, amount_cents, provider_ref, status, created_at)
              VALUES (?, ?, ?, ?, ?, 'success', ?)`)
    .run(id, receiptId, requestId, amountCents, providerRef, nowIso());
  return { reused: false, paymentId: id, providerRef, status: 'success', amountCents };
}
