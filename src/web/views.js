import { esc } from './util.js';
import { fmtDateTime, fmtTime, fmtDuration, fmtMoney, remainingMs } from '../domain/time.js';
import { maskPlate } from '../domain/plate.js';
import { REASON_TEXTS } from '../domain/billing.js';

const GREEN = '#0f5132';
const GREEN_SOFT = '#e6f4ea';

export function layout({ title, active, body, flash = null }) {
  const nav = [
    ['/', '邀请'],
    ['/gate', '门岗核验'],
    ['/inpark', '在园车辆'],
    ['/billing', '超时计费'],
    ['/exceptions', '异常放行'],
    ['/payments', '收费流水'],
  ];
  const flashHtml = flash ? `
    <div class="flash flash-${esc(flash.type)}">${esc(flash.message)}</div>` : '';
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · 园区访客车辆</title>
<style>
  :root { --green: ${GREEN}; --green-soft: ${GREEN_SOFT}; --ink: #2d333a; --muted: #6b7280; --line: #e3e6ea; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #fff; color: var(--ink);
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 15px; }
  header { border-bottom: 3px solid var(--green); background: #fff; }
  .bar { max-width: 1080px; margin: 0 auto; padding: 14px 20px; display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
  .brand { font-size: 18px; font-weight: 700; color: var(--green); white-space: nowrap; }
  nav { display: flex; gap: 4px; flex-wrap: wrap; }
  nav a { color: #374151; text-decoration: none; padding: 7px 13px; border-radius: 6px; font-size: 14px; }
  nav a:hover { background: var(--green-soft); }
  nav a.on { background: var(--green); color: #fff; }
  main { max-width: 1080px; margin: 22px auto; padding: 0 20px 60px; }
  h1 { font-size: 21px; margin: 0 0 16px; }
  h2 { font-size: 16px; margin: 26px 0 10px; padding-left: 10px; border-left: 4px solid var(--green); }
  .card { border: 1px solid var(--line); border-radius: 10px; padding: 18px; background: #fff; margin-bottom: 18px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 10px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; font-size: 13px; background: #fafbfc; }
  tr:last-child td { border-bottom: none; }
  label { display: block; font-size: 13px; color: var(--muted); margin: 10px 0 4px; }
  input, select, textarea { width: 100%; padding: 9px 10px; border: 1px solid #cbd2d9; border-radius: 7px;
    font-size: 15px; color: var(--ink); background: #fff; }
  textarea { min-height: 60px; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px 16px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; }
  button, .btn { display: inline-block; border: none; border-radius: 7px; padding: 9px 18px; font-size: 14px;
    cursor: pointer; background: var(--green); color: #fff; text-decoration: none; margin-top: 14px; }
  button.ghost, .btn.ghost { background: #fff; color: var(--green); border: 1px solid var(--green); }
  button.danger, .btn.danger { background: #fff; color: #b42318; border: 1px solid #f0b8b0; }
  .tag { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 12px; }
  .tag-green { background: var(--green-soft); color: var(--green); }
  .tag-gray { background: #f1f3f5; color: #4b5563; }
  .tag-red { background: #fdeceb; color: #b42318; }
  .tag-amber { background: #fdf3e1; color: #9a6200; }
  .muted { color: var(--muted); }
  .flash { padding: 11px 15px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
  .flash-ok { background: var(--green-soft); color: var(--green); border: 1px solid #bfe3cd; }
  .flash-err { background: #fdeceb; color: #b42318; border: 1px solid #f0b8b0; }
  .plate-big { font-family: "SFMono-Regular", Consolas, monospace; font-size: 34px; font-weight: 700;
    letter-spacing: 3px; color: #fff; background: var(--green); border-radius: 9px;
    padding: 14px 22px; display: inline-block; }
  .window-box { font-size: 17px; font-weight: 600; margin-top: 12px; }
  .window-box span { color: var(--green); }
  .rowline { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; }
  .money-big { font-size: 26px; font-weight: 700; color: #b42318; }
  .split td:nth-child(2), .split td:nth-child(3) { white-space: nowrap; }
  .reason-full { color: #374151; line-height: 1.55; }
  .photo { width: 180px; border: 1px solid var(--line); border-radius: 6px; }
  .inline { display: inline; }
  .inline button { margin: 0 0 0 6px; padding: 5px 12px; font-size: 13px; }
  .nowrap { white-space: nowrap; }
  .rest-remain { font-weight: 700; }
  .rest-over { color: #b42318; font-weight: 700; }
  code { background: #f3f5f7; padding: 1px 6px; border-radius: 4px; font-size: 13px; }
</style>
</head>
<body>
<header><div class="bar">
  <div class="brand">🅿️ 园区访客车辆通行</div>
  <nav>${nav.map(([href, label]) => `<a href="${href}"${href === active ? ' class="on"' : ''}>${label}</a>`).join('')}</nav>
</div></header>
<main>${flashHtml}${body}</main>
</body></html>`;
}

const statusTag = (s) => {
  const map = {
    valid: ['tag-green', '有效'], used: ['tag-gray', '已入场'], cancelled: ['tag-red', '已取消'],
    pending: ['tag-amber', '待处理'], approved: ['tag-green', '已核准'], rejected: ['tag-red', '已驳回/拦截'],
    paid: ['tag-green', '已支付'], free_gate: ['tag-green', '免费放行'],
    queued: ['tag-amber', '待重放'], replayed: ['tag-green', '已重放'], failed: ['tag-red', '重放被拦截'],
  };
  const [cls, txt] = map[s] || ['tag-gray', s];
  return `<span class="tag ${cls}">${txt}</span>`;
};

// ---------- 邀请 ----------
export function pageInvitations({ units, invitations, activeUnit, flash }) {
  const opts = units.map((u) =>
    `<option value="${esc(u.id)}"${u.id === activeUnit ? ' selected' : ''}>${esc(u.name)}（${esc(u.code)}）</option>`).join('');
  const rows = invitations.map((i) => `
    <tr>
      <td class="nowrap"><strong>${esc(i.plate)}</strong><br><span class="muted">遮罩 ${esc(maskPlate(i.plate))}</span></td>
      <td class="nowrap"><code>${esc(i.code)}</code><br>${statusTag(i.status)}</td>
      <td>${esc(i.unit_name)}<br><span class="muted">${esc(i.building)} · ${esc(i.contact_name)} ${esc(i.contact_phone)}</span></td>
      <td class="nowrap">${fmtDateTime(i.planned_arrival)}<br>至 ${fmtDateTime(i.planned_departure)}</td>
      <td class="nowrap">
        <form class="inline" method="post" action="/invitations/${esc(i.id)}/cancel"
              onsubmit="return confirm('确认取消该邀请？');">
          <input type="hidden" name="unitId" value="${esc(i.unit_id)}">
          <button class="danger" ${i.status !== 'valid' ? 'disabled' : ''}>取消</button>
        </form>
        <a class="btn ghost" href="/gate?code=${esc(i.code)}">去门岗</a>
      </td>
    </tr>`).join('');
  const body = `
  <h1>邀请（被访单位申请临时通行）</h1>
  <div class="card">
    <form method="post" action="/invitations">
      <input type="hidden" name="unitId" value="${esc(activeUnit || units[0]?.id || '')}">
      <div class="grid">
        <div><label>被访单位（只能管理本单位邀请）</label>
          <select name="unitId">${opts}</select></div>
        <div><label>车牌</label><input name="plate" placeholder="京A12345 / 粤B12345D" required></div>
        <div><label>访问楼栋</label><input name="building" placeholder="A座 101" required></div>
        <div><label>联系人</label><input name="contactName" placeholder="姓名" required></div>
        <div><label>联系电话</label><input name="contactPhone" placeholder="手机号" required></div>
        <div></div>
        <div><label>预计到达</label><input type="datetime-local" name="plannedArrival" required></div>
        <div><label>预计离场</label><input type="datetime-local" name="plannedDeparture" required></div>
      </div>
      <button>生成通行邀请</button>
      <span class="muted" style="margin-left:12px">同一车牌可有多份邀请；一张邀请仅一次有效入场。</span>
    </form>
  </div>
  <h2>邀请列表（${invitations.length}）</h2>
  <div class="card"><table>
    <thead><tr><th>车牌</th><th>通行码/状态</th><th>单位 / 楼栋 / 联系人</th><th>批准时段</th><th>操作</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5" class="muted">暂无邀请</td></tr>'}</tbody>
  </table></div>`;
  return layout({ title: '邀请', active: '/', body, flash });
}

// ---------- 门岗 ----------
export function pageGate({ units, result, query, flash, gateId }) {
  let resultHtml = '';
  if (result) {
    const { invitation: i, assessment, samePlateOthers, entry, exit } = result;
    const early = assessment.code.startsWith('EARLY');
    const verdictCls = ['OK', 'EARLY_WITHIN_GRACE'].includes(assessment.code) ? 'tag-green'
      : assessment.code === 'CODE_REUSED' ? 'tag-red'
      : ['EARLY_BEYOND_GRACE', 'WINDOW_EXPIRED', 'CANCELLED'].includes(assessment.code) ? 'tag-red' : 'tag-amber';
    resultHtml = `
    <div class="card">
      <div class="rowline">
        <div class="plate-big">${esc(i.plate)}</div>
        <div>
          <div style="font-size:14px" class="muted">通行码 <code>${esc(i.code)}</code> · 遮罩车牌 ${esc(maskPlate(i.plate))}</div>
          <div class="window-box">批准时段：<span>${fmtDateTime(i.planned_arrival)}</span> 至 <span>${fmtDateTime(i.planned_departure)}</span></div>
          <div style="margin-top:8px">${esc(i.unit_name)} · ${esc(i.building)} · 联系人 ${esc(i.contact_name)} ${esc(i.contact_phone)}</div>
        </div>
      </div>
      <hr style="border:none;border-top:1px solid var(--line);margin:16px 0">
      <div class="rowline">
        <span class="tag ${verdictCls}" style="font-size:14px;padding:5px 14px">${esc(assessment.message)}</span>
        ${entry ? `<span class="tag tag-gray">入场 ${fmtDateTime(entry.happened_at)}</span>` : ''}
        ${exit ? `<span class="tag tag-gray">出场 ${fmtDateTime(exit.happened_at)}</span>` : ''}
      </div>
      <div style="margin-top:14px" class="rowline">
        <form class="inline" method="post" action="/gate/entry">
          <input type="hidden" name="code" value="${esc(i.code)}">
          <input type="hidden" name="observedPlate" value="${esc(i.plate)}">
          <input type="hidden" name="gateId" value="${esc(gateId)}">
          <button ${entry || i.status === 'cancelled' || ['EARLY_BEYOND_GRACE', 'WINDOW_EXPIRED'].includes(assessment.code) ? 'disabled' : ''}>确认车证相符，放行入场</button>
        </form>
        <form class="inline" method="post" action="/gate/exit">
          <input type="hidden" name="code" value="${esc(i.code)}">
          <input type="hidden" name="gateId" value="${esc(gateId)}">
          <button class="ghost" ${!entry || exit ? 'disabled' : ''}>车辆离场（结算）</button>
        </form>
        <a class="btn ghost" href="/exceptions/new?invitationId=${esc(i.id)}&direction=${entry && !exit ? 'exit' : 'entry'}&plate=${esc(i.plate)}">登记异常放行</a>
      </div>
      ${samePlateOthers.length ? `
        <h2>同车牌的其他邀请（${samePlateOthers.length}）</h2>
        <table><thead><tr><th>通行码</th><th>单位/楼栋</th><th>批准时段</th><th>状态</th></tr></thead><tbody>
        ${samePlateOthers.map((o) => `<tr><td><code>${esc(o.code)}</code></td><td>${esc(o.unit_name || '')} ${esc(o.building || '')}</td>
          <td class="nowrap">${fmtDateTime(o.planned_arrival)} ~ ${fmtTime(o.planned_departure)}</td><td>${statusTag(o.status)}</td></tr>`).join('')}
        </tbody></table>` : ''}
    </div>`;
  }
  const body = `
  <h1>门岗核验</h1>
  <div class="card">
    <form method="get" action="/gate" class="grid-2">
      <div><label>通行码</label><input name="code" value="${esc(query.code || '')}" placeholder="输入 6 位通行码"></div>
      <div><label>或 车牌</label><input name="plate" value="${esc(query.plate || '')}" placeholder="京A12345"></div>
      <div><label>门岗</label><select name="gateId">
        ${['GATE-01', 'GATE-02'].map((g) => `<option ${g === gateId ? 'selected' : ''}>${g}</option>`).join('')}
      </select></div>
      <div style="display:flex;align-items:flex-end"><button>核验</button></div>
    </form>
    <p class="muted" style="margin-bottom:0">门岗核对车辆与批准时段后放行；通行码转发给他人无效（一张邀请只能入场一次）。</p>
  </div>
  ${resultHtml}`;
  return layout({ title: '门岗核验', active: '/gate', body, flash });
}

// ---------- 在园车辆 ----------
export function pageInPark({ cars, flash }) {
  const now = Date.now();
  const rows = cars.map((c) => {
    const ms = remainingMs(c.planned_departure, new Date(now));
    const over = ms < 0;
    const mins = Math.round(Math.abs(ms) / 60000);
    return `<tr>
      <td class="nowrap"><strong>${esc(c.plate)}</strong><br><a href="/photos/${esc(c.photo_path || '')}" target="_blank"><img class="photo" src="/photos/${esc(c.photo_path || '')}" alt="遮罩车牌照片"></a></td>
      <td><code>${esc(c.code)}</code></td>
      <td>${esc(c.unit_name)}<br><span class="muted">${esc(c.building)} · ${esc(c.contact_name)} ${esc(c.contact_phone)}</span></td>
      <td class="nowrap">入场 ${fmtDateTime(c.entered_at)}<br>批准至 ${fmtDateTime(c.planned_departure)}</td>
      <td class="nowrap ${over ? 'rest-over' : 'rest-remain'}">
        ${over ? `已超时 ${fmtDuration(mins)}` : `剩余 ${fmtDuration(mins)}`}</td>
      <td class="nowrap">
        <form class="inline" method="post" action="/gate/exit">
          <input type="hidden" name="code" value="${esc(c.code)}">
          <button class="ghost">离场结算</button>
        </form>
      </td>
    </tr>`;
  }).join('');
  const body = `
  <h1>在园车辆（${cars.length}）</h1>
  <div class="card"><table>
    <thead><tr><th>车牌 / 遮罩照片</th><th>通行码</th><th>单位 / 联系人</th><th>入场 / 批准截止</th><th>剩余时间</th><th></th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="muted">当前没有在园车辆</td></tr>'}</tbody>
  </table></div>`;
  return layout({ title: '在园车辆', active: '/inpark', body, flash });
}

// ---------- 超时计费 ----------
function detailRow(label, mins, strong = false) {
  return `<tr ${strong ? 'style="font-weight:700"' : ''}><td>${label}</td><td>${fmtDuration(Math.max(0, mins))}</td><td></td></tr>`;
}

export function pageBilling({ receipts, flash }) {
  const cards = receipts.map((r) => {
    const d = JSON.parse(r.calc_detail);
    return `<div class="card">
      <div class="rowline" style="justify-content:space-between">
        <div>
          <strong style="font-size:17px">${esc(r.plate)}</strong>
          <code>${esc(r.code_hint || '')}</code>
          ${statusTag(r.status)}
          <div class="muted" style="margin-top:4px">${esc(r.unit_name)} · ${esc(r.building)} · 回执 ${esc(r.id.slice(-8))}</div>
        </div>
        <div style="text-align:right">
          <div class="muted" style="font-size:13px">超时应付</div>
          <div class="money-big">${fmtMoney(r.amount_cents)}</div>
        </div>
      </div>
      <table class="split" style="margin-top:12px">
        <thead><tr><th>费用拆分</th><th>时长</th><th>金额</th></tr></thead>
        <tbody>
          <tr><td>实际停留（${fmtDateTime(r.entered_at)} → ${fmtDateTime(r.exited_at)}${d.overnight ? '，<strong>跨夜停留</strong>' : ''}）</td>
              <td>${fmtDuration(d.totalMinutes)}</td><td>—</td></tr>
          <tr><td>批准时段内停留（免费）</td><td>${fmtDuration(d.approvedStayMinutes)}</td><td class="muted">¥0.00</td></tr>
          <tr><td>提前到达免费等待（≤ ${d.freeMinutesPerVisit ?? 30} 分钟）</td><td>${fmtDuration(d.earlyFreeGrace)}</td><td class="muted">¥0.00</td></tr>
          <tr><td>出场缓冲（批准结束后 ≤ 15 分钟，免费）</td><td>${fmtDuration(d.lateFreeGrace)}</td><td class="muted">¥0.00</td></tr>
          ${d.earlyOvertime ? `<tr><td>提前到达超出免费宽限（计费）</td><td>${fmtDuration(d.earlyOvertime)}</td><td></td></tr>` : ''}
          ${d.lateOvertime ? `<tr><td>离场超时（计费，不足 1 小时按 1 小时）</td><td>${fmtDuration(d.lateOvertime)}</td><td></td></tr>` : ''}
          ${d.waiver ? `<tr style="color:var(--green);font-weight:700"><td colspan="3">异常免费放行减免：${esc(d.waiverReason || '')}（园区管理员已复核）</td></tr>` : ''}
          <tr style="font-weight:700"><td>免费时间合计</td><td>${fmtDuration(r.free_minutes)}</td><td class="muted">¥0.00</td></tr>
          <tr style="font-weight:700"><td>超时计费时间</td><td>${fmtDuration(r.overtime_minutes)}</td>
              <td style="color:#b42318">${fmtMoney(r.amount_cents)}</td></tr>
          ${d.capped ? `<tr class="muted"><td colspan="3">已按跨自然日 ${d.capDays} 天单日封顶（¥60.00/天），原价 ${fmtMoney(d.rawAmountCents)}，实收 ${fmtMoney(r.amount_cents)}</td></tr>` : ''}
        </tbody>
      </table>
      <div class="rowline" style="margin-top:12px">
        ${r.status === 'pending' ? `
          <form class="inline" method="post" action="/billing/${esc(r.id)}/pay">
            <button>模拟支付 ${fmtMoney(r.amount_cents)} 并开闸</button>
          </form>
          <form class="inline" method="post" action="/billing/${esc(r.id)}/pay?fail=1">
            <button class="ghost" type="submit" title="模拟支付成功但闸机未开">模拟“支付后闸机未开”</button>
          </form>
          <a class="btn danger" href="/exceptions/new?invitationId=${esc(r.invitation_id)}&direction=exit&plate=${esc(r.plate)}&reason=OVERTIME_WAIVER">申请超时减免（管理员复核）</a>
        ` : `<span class="tag tag-green">${r.status === 'paid' ? '已完成支付与放行' : '已免费放行'}</span>
             <form class="inline" method="post" action="/billing/${esc(r.id)}/retry-gate">
               <button class="ghost">重试开闸（不重复收费）</button>
             </form>`}
      </div>
    </div>`;
  }).join('');
  const body = `
  <h1>超时计费（${receipts.length} 张回执）</h1>
  <p class="muted">出场按批准时段结算：清楚拆分免费时间与超时金额；重复提交回执不会重复收费。</p>
  ${cards || '<div class="card muted">暂无出场回执</div>'}`;
  return layout({ title: '超时计费', active: '/billing', body, flash });
}

// ---------- 异常放行 ----------
export function exceptionForm({ units, invitationId, plate, direction, reason, invitations, flash }) {
  const reasonOpts = Object.entries(REASON_TEXTS)
    .filter(([code]) => !['OFFLINE_REPLAY_REJECTED'].includes(code))
    .map(([code, text]) => `<option value="${code}"${code === reason ? ' selected' : ''}>${esc(text)}</option>`).join('');
  const invOpts = invitations.map((i) =>
    `<option value="${esc(i.id)}"${i.id === invitationId ? ' selected' : ''}>${esc(i.plate)} · ${esc(i.code)} · ${esc(i.unit_name)} · ${fmtDateTime(i.planned_arrival)}</option>`).join('');
  const body = `
  <h1>登记异常放行</h1>
  <div class="card"><form method="post" action="/exceptions">
    <div class="grid">
      <div><label>关联邀请（可空，用于无邀请车辆）</label><select name="invitationId"><option value="">— 无关联邀请 —</option>${invOpts}</select></div>
      <div><label>车牌（无邀请时填写）</label><input name="plate" value="${esc(plate || '')}" placeholder="京A12345"></div>
      <div><label>放行方向</label><select name="direction">
        <option value="entry"${direction === 'entry' ? ' selected' : ''}>入场</option>
        <option value="exit"${direction === 'exit' ? ' selected' : ''}>出场</option></select></div>
      <div><label>门岗</label><select name="gateId"><option>GATE-01</option><option>GATE-02</option></select></div>
      <div><label>登记人（门岗/被访单位）</label><input name="requestedBy" value="门岗值班员" required></div>
    </div>
    <label>异常理由（完整文字，将原样显示在处置记录中）</label>
    <select name="reasonCode">${reasonOpts}</select>
    <label>现场补充说明</label><textarea name="note" placeholder="例如：被访单位已电话确认接待"></textarea>
    <button>提交复核</button>
    <span class="muted" style="margin-left:12px">异常免费放行必须由园区管理员复核后才能开闸。</span>
  </form></div>
  <p><a href="/exceptions">← 返回异常放行列表</a></p>`;
  return layout({ title: '登记异常放行', active: '/exceptions', body, flash });
}

export function pageExceptions({ exceptions, flash }) {
  const rows = exceptions.map((x) => `
    <tr>
      <td class="nowrap">${esc(x.plate || '—')}<br><span class="muted">${x.direction === 'entry' ? '入场' : '出场'} · ${esc(x.gate_id || '')}</span></td>
      <td class="reason-full">${esc(x.reason_text)}${x.note ? `<div class="muted" style="margin-top:5px">现场说明：${esc(x.note)}</div>` : ''}</td>
      <td class="nowrap">${esc(x.requested_by)}<br><span class="muted">${fmtDateTime(x.created_at)}</span></td>
      <td class="nowrap">${statusTag(x.status)}
        ${x.reviewed_by_name ? `<div class="muted" style="margin-top:5px">${esc(x.reviewed_by_name)}<br>${fmtDateTime(x.reviewed_at)}</div>` : ''}
        ${x.review_note ? `<div class="muted">${esc(x.review_note)}</div>` : ''}</td>
      <td class="nowrap">
        ${x.status === 'pending' ? `
        <form class="inline" method="post" action="/exceptions/${esc(x.id)}/review">
          <input type="hidden" name="approve" value="1">
          <button>管理员核准放行</button>
        </form>
        <form class="inline" method="post" action="/exceptions/${esc(x.id)}/review">
          <input type="hidden" name="approve" value="0">
          <button class="danger">驳回</button>
        </form>` : ''}
      </td>
    </tr>`).join('');
  const body = `
  <div class="rowline" style="justify-content:space-between">
    <h1 style="margin:0">异常放行处置记录（${exceptions.length}）</h1>
    <a class="btn" href="/exceptions/new">登记异常放行</a>
  </div>
  <div class="card" style="margin-top:16px"><table>
    <thead><tr><th>车牌/方向</th><th>异常理由（完整文字）</th><th>登记人/时间</th><th>复核状态</th><th>园区管理员操作</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5" class="muted">暂无异常记录</td></tr>'}</tbody>
  </table></div>`;
  return layout({ title: '异常放行', active: '/exceptions', body, flash });
}

// ---------- 收费流水 ----------
export function pagePayments({ payments, gateLogs, offlineQueue, flash }) {
  const payRows = payments.map((p) => `
    <tr><td class="nowrap">${fmtDateTime(p.created_at)}</td><td><strong>${esc(p.plate)}</strong></td>
    <td class="nowrap">${fmtMoney(p.amount_cents)}</td>
    <td class="nowrap"><span class="tag tag-green">${esc(p.status)}</span></td>
    <td class="nowrap mono">${esc(p.provider_ref)}</td>
    <td class="nowrap muted"><code>${esc(p.request_id)}</code></td></tr>`).join('');
  const gateRows = gateLogs.map((g) => `
    <tr><td class="nowrap">${fmtDateTime(g.created_at)}</td><td>${esc(g.gate_id)}</td>
    <td>${g.action === 'open' ? '<span class="tag tag-green">开闸</span>'
      : g.action === 'denied' ? '<span class="tag tag-red">拒绝</span>'
      : '<span class="tag tag-amber">开闸失败</span>'}</td>
    <td>${esc(g.plate || '—')}</td><td class="reason-full">${esc(g.context || '')}</td></tr>`).join('');
  const qRows = offlineQueue.map((q) => `
    <tr><td class="nowrap">${fmtDateTime(q.created_at)}</td><td>${statusTag(q.status)}</td>
    <td class="muted"><code>${esc(q.event_id)}</code></td>
    <td>${q.replayed_at ? fmtDateTime(q.replayed_at) : '—'}</td></tr>`).join('');
  const body = `
  <h1>收费流水</h1>
  <h2>支付记录（模拟支付适配器，request_id 幂等）</h2>
  <div class="card"><table>
    <thead><tr><th>时间</th><th>车牌</th><th>金额</th><th>状态</th><th>支付渠道流水</th><th>幂等请求号</th></tr></thead>
    <tbody>${payRows || '<tr><td colspan="6" class="muted">暂无支付</td></tr>'}</tbody></table></div>
  <h2>闸机动作日志（模拟闸机适配器）</h2>
  <div class="card"><table>
    <thead><tr><th>时间</th><th>门岗</th><th>动作</th><th>车牌</th><th>上下文</th></tr></thead>
    <tbody>${gateRows || '<tr><td colspan="5" class="muted">暂无日志</td></tr>'}</tbody></table></div>
  <h2>门禁离线事件队列 / 重放</h2>
  <div class="rowline"><form class="inline" method="post" action="/offline/enqueue">
      <input type="hidden" name="kind" value="entry"><button class="ghost" type="submit">模拟：离线期间登记一条入场</button>
    </form>
    <form class="inline" method="post" action="/offline/replay"><button>恢复在线：重放离线队列</button></form>
    <span class="muted">重放按 event_id 去重，重复/已用邀请会被拦截并写入异常记录。命令行样例：<code>npm run demo</code></span>
  </div>
  <div class="card" style="margin-top:12px"><table>
    <thead><tr><th>离线登记时间</th><th>状态</th><th>事件幂等号</th><th>重放时间</th></tr></thead>
    <tbody>${qRows || '<tr><td colspan="4" class="muted">离线队列为空</td></tr>'}</tbody></table></div>`;
  return layout({ title: '收费流水', active: '/payments', body, flash });
}
