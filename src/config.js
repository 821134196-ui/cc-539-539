import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(__dirname, '..');
export const DATA_DIR = process.env.PGV_DATA_DIR || path.join(ROOT, 'data');
export const PHOTO_DIR = path.join(DATA_DIR, 'photos');
export const DB_PATH = process.env.PGV_DB_PATH || path.join(DATA_DIR, 'park.db');
export const PORT = Number(process.env.PGV_PORT || 8080);

// 计费规则：出场按批准时段计算超时
export const PRICING = {
  freeExitGraceMinutes: 15,   // 批准结束后 15 分钟内离场免费（出场缓冲）
  ratePerHourCents: 500,      // 每超时小时 5.00 元
  freeMinutesPerVisit: 30,    // 每张邀请的免费停车时长（在批准时段内提前到达的免费等待）
  capPerDayCents: 6000,       // 单日封顶 60 元（跨夜按自然停留日计）
};

export const GRACE_MINUTES_EARLY = 30; // 提前到达宽限：早于预计到达 30 分钟内可入场
