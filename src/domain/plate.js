// 车牌规范化与遮罩
// 支持普通蓝牌（京A12345）、新能源（粤B12345D / 沪AD12345）等常见格式
export function normalizePlate(input) {
  return String(input || '').replace(/[\s\-·.]+/g, '').toUpperCase();
}

export function isValidPlate(plate) {
  const p = normalizePlate(plate);
  // 省份简称 + 字母 + 5~6 位字母数字（新能源 6 位）
  return /^[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领][A-Z][A-Z0-9]{5,6}$/.test(p);
}

// 遮罩：京A•***45 形式；本地存遮罩照片文件名也用此
export function maskPlate(plate) {
  const p = normalizePlate(plate);
  if (p.length < 4) return p;
  const head = p.slice(0, 2);
  const tail = p.slice(-2);
  return `${head}${'*'.repeat(Math.max(3, p.length - 4))}${tail}`;
}
