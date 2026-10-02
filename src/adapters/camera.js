import fs from 'node:fs';
import path from 'node:path';
import { PHOTO_DIR } from '../config.js';
import { maskPlate } from '../domain/plate.js';
import { cid } from '../db.js';

// 模拟摄像头：生成一张本地“车牌遮罩照片”（SVG，含遮罩车牌、时间、门岗）
// 真实项目这里替换为抓拍 SDK；落库的永远是遮照，不保存原图。
export function captureMaskedPhoto({ plate, gateId, atIso }) {
  fs.mkdirSync(PHOTO_DIR, { recursive: true });
  const masked = maskPlate(plate);
  const stamp = new Date(atIso).toLocaleString('zh-CN', { hour12: false });
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="160" viewBox="0 0 480 160">
  <rect width="480" height="160" fill="#1f2933"/>
  <rect x="30" y="45" width="420" height="70" rx="8" fill="#0f5132"/>
  <text x="240" y="95" font-family="monospace" font-size="40" fill="#e6f4ea"
        text-anchor="middle" letter-spacing="4">${masked}</text>
  <text x="30" y="25" font-family="sans-serif" font-size="15" fill="#9aa5b1">抓拍门岗：${gateId ?? 'GATE-01'}（车牌已遮罩）</text>
  <text x="30" y="140" font-family="sans-serif" font-size="14" fill="#9aa5b1">${stamp}</text>
</svg>`;
  const file = `${cid('ph_')}.svg`;
  fs.writeFileSync(path.join(PHOTO_DIR, file), svg, 'utf8');
  return file;
}
