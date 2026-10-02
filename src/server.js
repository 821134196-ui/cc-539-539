import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { URL } from 'node:url';
import { getDb } from './db.js';
import { PHOTO_DIR, PORT, DB_PATH } from './config.js';
import { parseForm, esc } from './web/util.js';
import * as views from './web/views.js';
import * as invSvc from './domain/invitations.js';
import * as acc from './domain/access.js';
import { AppError } from './domain/errors.js';
import { _simulateGateFailOnce } from './adapters/gate.js';
import { cid } from './db.js';

export function createServer(db = getDb()) {
  const send = (res, status, html, headers = {}) => {
    res.writeHead(status, { 'content-type': 'text/html; charset=utf-8', ...headers });
    res.end(html);
  };
  const redirect = (res, location) => {
    res.writeHead(303, { location });
    res.end();
  };
  const flashUrl = (base, type, message) =>
    `${base}?flash=${encodeURIComponent(type)}&msg=${encodeURIComponent(message)}`;

  async function readBody(req) {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    return Buffer.concat(chunks).toString('utf8');
  }

  const flash = (urlObj) => {
    const t = urlObj.searchParams.get('flash');
    const m = urlObj.searchParams.get('msg');
    return t && m ? { type: t === 'err' ? 'err' : 'ok', message: m } : null;
  };

  const server = http.createServer(async (req, res) => {
    const urlObj = new URL(req.url, 'http://localhost');
    const p = urlObj.pathname;
    const f = flash(urlObj);
    try {
      // 静态：遮罩照片
      if (req.method === 'GET' && p.startsWith('/photos/')) {
        const name = path.basename(p);
        const file = path.join(PHOTO_DIR, name);
        if (!file.startsWith(PHOTO_DIR) || !fs.existsSync(file)) { res.writeHead(404); return res.end('not found'); }
        res.writeHead(200, { 'content-type': 'image/svg+xml' });
        return res.end(fs.readFileSync(file));
      }

      // ---------- GET ----------
      if (req.method === 'GET') {
        if (p === '/') {
          const units = invSvc.listUnits(db);
          const activeUnit = urlObj.searchParams.get('unit') || units[0]?.id;
          const invitations = activeUnit
            ? db.prepare(`SELECT i.*, u.name AS unit_name, u.code AS unit_code FROM invitations i
                          JOIN units u ON u.id = i.unit_id WHERE i.unit_id = ? ORDER BY i.created_at DESC`).all(activeUnit)
            : [];
          return send(res, 200, views.pageInvitations({ units, invitations, activeUnit, flash: f }));
        }
        if (p === '/gate') {
          const gateId = urlObj.searchParams.get('gateId') || 'GATE-01';
          const code = urlObj.searchParams.get('code')?.trim();
          const plate = urlObj.searchParams.get('plate')?.trim();
          let result = null;
          if (code || plate) {
            result = acc.verifyAtGate({ code: code || undefined, plate: plate || undefined, gateId }, db);
          }
          return send(res, 200, views.pageGate({ units: invSvc.listUnits(db), result, query: { code, plate }, flash: f, gateId }));
        }
        if (p === '/inpark') {
          return send(res, 200, views.pageInPark({ cars: acc.listInPark(db), flash: f }));
        }
        if (p === '/billing') {
          return send(res, 200, views.pageBilling({ receipts: acc.listReceipts(db), flash: f }));
        }
        if (p === '/exceptions') {
          return send(res, 200, views.pageExceptions({ exceptions: acc.listExceptions(null, db), flash: f }));
        }
        if (p === '/exceptions/new') {
          return send(res, 200, views.exceptionForm({
            units: invSvc.listUnits(db),
            invitationId: urlObj.searchParams.get('invitationId') || '',
            plate: urlObj.searchParams.get('plate') || '',
            direction: urlObj.searchParams.get('direction') || 'entry',
            reason: urlObj.searchParams.get('reason') || 'EARLY_ARRIVAL',
            invitations: invSvc.listInvitations({ includeAll: true }, db),
            flash: f,
          }));
        }
        if (p === '/payments') {
          return send(res, 200, views.pagePayments({
            payments: acc.listPayments(db),
            gateLogs: acc.listGateLogs(40, db),
            offlineQueue: acc.listOfflineQueue(db),
            flash: f,
          }));
        }
        res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
        return res.end('404');
      }

      // ---------- POST ----------
      const body = parseForm(await readBody(req));

      if (p === '/invitations') {
        const i = invSvc.createInvitation(body, db);
        return redirect(res, flashUrl('/', 'ok', `邀请已生成：车牌 ${i.plate}，通行码 ${i.code}（请转交车主，勿公开转发）`));
      }

      let m;
      if ((m = p.match(/^\/invitations\/([\w-]+)\/cancel$/))) {
        invSvc.cancelInvitation(m[1], body.unitId, db);
        return redirect(res, flashUrl('/', 'ok', '邀请已取消'));
      }

      if (p === '/gate/entry') {
        const out = acc.admitEntry({ code: body.code, observedPlate: body.observedPlate, gateId: body.gateId || 'GATE-01' }, db);
        return redirect(res, flashUrl('/gate', 'ok',
          out.duplicated ? '该入场事件已存在（幂等返回），未重复入场'
            : `放行成功：${out.invitation.plate} 已入场，遮罩照片已保存`));
      }
      if (p === '/gate/exit') {
        const out = acc.processExit({ code: body.code, gateId: body.gateId || 'GATE-01' }, db);
        if (out.duplicated) {
          return redirect(res, flashUrl('/billing', 'ok', '回执已存在：出场回执重试未重复收费，直接返回原结果'));
        }
        if (out.receipt.amount_cents === 0) {
          return redirect(res, flashUrl('/billing', 'ok', '在批准时段（含免费缓冲）内离场，免费放行'));
        }
        return redirect(res, flashUrl('/billing', 'err',
          `离场超时 ${out.detail.overtimeMinutes} 分钟，应付 ¥${(out.receipt.amount_cents / 100).toFixed(2)}，请在超时计费页支付`));
      }

      if ((m = p.match(/^\/billing\/([\w-]+)\/pay$/))) {
        if (urlObj.searchParams.get('fail') === '1') _simulateGateFailOnce();
        const out = acc.payAndOpen({ receiptId: m[1], requestId: `web-${m[1]}` }, db);
        if (!out.gate.opened) {
          return redirect(res, flashUrl('/billing', 'err', '支付已成功扣款，但闸机未开启！可点击“重试开闸”，不会重复收费'));
        }
        return redirect(res, flashUrl('/billing', 'ok', out.retried ? '重试开闸成功，未产生二次扣费' : '支付成功，闸机已开启，祝一路平安'));
      }
      if ((m = p.match(/^\/billing\/([\w-]+)\/retry-gate$/))) {
        const out = acc.payAndOpen({ receiptId: m[1], requestId: `web-${m[1]}` }, db);
        return redirect(res, flashUrl('/billing',
          out.gate.opened ? 'ok' : 'err', out.gate.opened ? '闸机重试开启成功，未重复收费' : '闸机仍未开启，请联系门岗人工处置'));
      }

      if (p === '/exceptions') {
        acc.recordException({
          plate: body.plate || null,
          invitationId: body.invitationId || null,
          reasonCode: body.reasonCode,
          direction: body.direction,
          gateId: body.gateId,
          requestedBy: body.requestedBy,
          note: body.note || '',
        }, db);
        return redirect(res, flashUrl('/exceptions', 'ok', '异常放行已登记，等待园区管理员复核（复核前不开闸）'));
      }
      if ((m = p.match(/^\/exceptions\/([\w-]+)\/review$/))) {
        const out = acc.reviewException(m[1], 'admin-1', body.approve === '1', body.reviewNote || '', db);
        return redirect(res, flashUrl('/exceptions', 'ok',
          out.exception.status === 'approved'
            ? `园区管理员已核准，闸机${out.gate?.opened ? '已开启' : '开启失败请重试'}`
            : '园区管理员已驳回该异常放行'));
      }

      if (p === '/offline/enqueue') {
        // 取一张当前有效邀请模拟“离线期间车辆到场”
        const candidate = db.prepare("SELECT * FROM invitations WHERE status = 'valid' ORDER BY created_at DESC LIMIT 1").get();
        if (!candidate) return redirect(res, flashUrl('/payments', 'err', '当前没有有效邀请可用于离线演示，请先在邀请页创建一张'));
        const { eventId } = acc.queueOfflineEvent({
          kind: 'entry', code: candidate.code, gateId: 'GATE-01',
          at: new Date().toISOString(),
        }, db);
        return redirect(res, flashUrl('/payments', 'ok',
          `门禁离线：车辆 ${candidate.plate} 的入场已登记到本地队列（event_id ${eventId.slice(-8)}），恢复后点重放`));
      }
      if (p === '/offline/replay') {
        const results = acc.replayOffline(db);
        const ok = results.filter((r) => r.ok).length;
        const dup = results.filter((r) => r.ok && r.duplicated).length;
        const bad = results.filter((r) => !r.ok).length;
        return redirect(res, flashUrl('/payments', bad ? 'err' : 'ok',
          `离线重放完成：成功 ${ok} 条（其中幂等去重 ${dup} 条），拦截 ${bad} 条（拦截记录见异常放行页）`));
      }

      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      res.end('404');
    } catch (err) {
      if (err instanceof AppError) {
        return send(res, 400, layoutErr(err.message, err.code), {});
      }
      console.error(err);
      res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' });
      res.end('服务器错误：' + esc(err.message));
    }
  });
  return server;
}

function layoutErr(message, code) {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
  <body style="font-family:sans-serif;padding:40px;max-width:680px;margin:0 auto">
  <h2 style="color:#b42318">操作被拒绝（${esc(code)}）</h2>
  <p style="font-size:16px">${esc(message)}</p>
  <p><a href="/" style="color:#0f5132">← 返回首页</a></p></body></html>`;
}

export function start(port = PORT, db = getDb()) {
  const server = createServer(db);
  server.listen(port, () => {
    console.log(`🅿️  园区访客车辆系统已启动：http://localhost:${port}`);
    console.log(`    SQLite 数据库：${DB_PATH}`);
  });
  return server;
}
