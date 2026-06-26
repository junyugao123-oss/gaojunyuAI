#!/usr/bin/env node
import { stocks } from 'stock-api';

function parseArgs(argv) {
  const args = new Map();
  for (let index = 2; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) continue;
    const next = argv[index + 1];
    args.set(key.slice(2), next && !next.startsWith('--') ? next : 'true');
    if (next && !next.startsWith('--')) index += 1;
  }
  return args;
}

function toStockApiCode(rawCode) {
  const code = String(rawCode || '').trim().toUpperCase().replace(/\s+/g, '');
  if (/^HK\d{1,5}$/.test(code)) {
    return `HK${code.slice(2).padStart(5, '0')}`;
  }
  if (/^\d{1,5}\.HK$/.test(code)) {
    return `HK${code.split('.')[0].padStart(5, '0')}`;
  }
  if (/^\d{6}\.(SH|SS|SZ|BJ)$/.test(code)) {
    const [digits, suffix] = code.split('.');
    return `${suffix === 'SS' ? 'SH' : suffix}${digits}`;
  }
  if (/^(SH|SZ|BJ)\d{6}$/.test(code)) {
    return code;
  }
  if (/^\d{6}$/.test(code)) {
    if (/^[659]/.test(code)) return `SH${code}`;
    if (/^[8]/.test(code)) return `BJ${code}`;
    return `SZ${code}`;
  }
  return code;
}

function normalizeKline(row) {
  return {
    date: row.date,
    open: Number(row.open),
    close: Number(row.close),
    high: Number(row.high),
    low: Number(row.low),
    volume: Number(row.volume || 0),
    source: row.source || null,
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const code = toStockApiCode(args.get('code'));
  const count = Number.parseInt(args.get('count') || '160', 10);
  const period = args.get('period') || 'day';

  if (!code) {
    throw new Error('missing --code');
  }

  const stock = await stocks.auto.getStock(code);
  const klines = await stocks.auto.getKlines(code, {
    period,
    count: Number.isFinite(count) ? count : 160,
    adjust: 'qfq',
  }).catch(() => []);

  process.stdout.write(JSON.stringify({
    provider: 'stock-api',
    providerSource: stock.source || 'auto',
    providerCode: code,
    stock,
    klines: Array.isArray(klines) ? klines.map(normalizeKline) : [],
  }));
}

main().catch((error) => {
  process.stderr.write(`${error?.name || 'Error'}: ${error?.message || String(error)}\n`);
  process.exit(1);
});
