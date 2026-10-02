// 时间工具：统一用本地时区 ISO 字符串存储（带偏移），展示时格式化
export function nowIso() {
  return new Date().toISOString();
}

export function parseTime(s) {
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) throw new Error(`非法时间: ${s}`);
  return d;
}

export function fmtDateTime(s) {
  if (!s) return '—';
  const d = parseTime(s);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtTime(s) {
  const d = parseTime(s);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function minutesBetween(a, b) {
  return Math.round((parseTime(b) - parseTime(a)) / 60000);
}

// 跨夜判断
export function isOvernight(start, end) {
  const a = parseTime(start); const b = parseTime(end);
  return new Date(a.getFullYear(), a.getMonth(), a.getDate()).getTime()
    !== new Date(b.getFullYear(), b.getMonth(), b.getDate()).getTime();
}

export function addMinutes(s, m) {
  const d = parseTime(s);
  d.setMinutes(d.getMinutes() + m);
  return d.toISOString();
}

// 剩余时间（毫秒），用于在园列表
export function remainingMs(approvedEnd, now = new Date()) {
  return parseTime(approvedEnd) - now;
}

export function fmtDuration(mins) {
  if (mins <= 0) return '0 分钟';
  const d = Math.floor(mins / 1440);
  const h = Math.floor((mins % 1440) / 60);
  const m = mins % 60;
  const parts = [];
  if (d) parts.push(`${d} 天`);
  if (h) parts.push(`${h} 小时`);
  if (m || parts.length === 0) parts.push(`${m} 分钟`);
  return parts.join(' ');
}

export function fmtMoney(cents) {
  return `¥${(cents / 100).toFixed(2)}`;
}
