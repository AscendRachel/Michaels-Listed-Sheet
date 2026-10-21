// Drag-fill series, like the fill handle in Google Sheets.
//   SKU123            -> SKU124, SKU125 ...        (a trailing number counts up, zero padding is kept: GT-0099 -> GT-0100)
//   10, 20 (two cells) -> 30, 40 ...                 (two or more numbers continue their step)
//   5 (one plain number) -> 5, 5 ...                 (a single plain number is copied, as in Sheets)
//   anything else      -> the selected values repeat in order
// `src` is the selected cells top to bottom (as text); returns `n` new values.

const TAIL = /^(.*?)(\d+)(\D*)$/s;

function parse(s) {
  const m = TAIL.exec(s);
  if (!m) return null;
  const [, pre, digits, post] = m;
  const padded = digits.length > 1 && digits.startsWith("0");
  return { pre, post, num: Number(digits), width: padded ? digits.length : 0, plain: pre === "" && post === "" };
}

function plainNumber(s) {
  const t = String(s).trim().replace(/^\$/, "").replace(/,/g, "");
  return t !== "" && /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : null;
}

const fmtNum = (x) => String(Math.round(x * 1e8) / 1e8);

export function nextSeries(src, n) {
  const cycle = () => Array.from({ length: n }, (_, i) => src[i % src.length]);
  if (!src.length || n <= 0) return [];
  // plain numbers, including decimals: continue a step shared by 2+ cells, copy a single one
  const nums = src.map(plainNumber);
  if (nums.every((x) => x !== null)) {
    if (nums.length < 2) return cycle();
    const step = nums[1] - nums[0];
    if (!nums.every((x, i) => i === 0 || Math.abs(x - nums[i - 1] - step) < 1e-9)) return cycle();
    const dollar = String(src[0]).trim().startsWith("$");
    return Array.from({ length: n }, (_, i) => (dollar ? "$" : "") + fmtNum(nums[nums.length - 1] + step * (i + 1)));
  }
  // text with a trailing number (SKU123, GT-0001, Item 7, 12pcs)
  const parts = src.map(parse);
  if (parts.every(Boolean) && parts.every((p) => p.pre === parts[0].pre && p.post === parts[0].post)) {
    let step = 1;
    if (parts.length >= 2) {
      step = parts[1].num - parts[0].num;
      if (!parts.every((p, i) => i === 0 || p.num - parts[i - 1].num === step)) return cycle();
    }
    const last = parts[parts.length - 1], width = Math.max(...parts.map((p) => p.width));
    return Array.from({ length: n }, (_, i) => {
      const v = last.num + step * (i + 1);
      if (v < 0) return src[i % src.length];
      return last.pre + (width ? String(v).padStart(width, "0") : String(v)) + last.post;
    });
  }
  return cycle();
}
