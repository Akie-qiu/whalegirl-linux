'use strict';

/**
 * feed 的格式解析：纯文本 / txt / csv / json / QQ 导出 / 微信导出。
 * 只解析，不做匿名化；敏感信息过滤由 store.js 负责。
 */

const fs = require('fs');
const path = require('path');

class ImportersError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'ImportersError';
    this.code = code;
  }
}

const SOURCES = new Set(['bili', 'qq', 'wechat', 'text']);
const KINDS = new Set(['comment', 'reply', 'danmaku', 'chat', 'line']);

function decodeBuffer(buf) {
  if (!Buffer.isBuffer(buf)) return String(buf);
  if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) {
    return new TextDecoder('utf-16le').decode(buf.subarray(2));
  }
  if (buf.length >= 2 && buf[0] === 0xfe && buf[1] === 0xff) {
    return new TextDecoder('utf-16be').decode(buf.subarray(2));
  }
  const body = buf.length >= 3 && buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf
    ? buf.subarray(3)
    : buf;
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(body);
  } catch (error) {
    try {
      return new TextDecoder('gbk').decode(body);
    } catch (gbkError) {
      return new TextDecoder('utf-8').decode(body);
    }
  }
}

function readFileText(filePath) {
  try {
    return decodeBuffer(fs.readFileSync(filePath));
  } catch (error) {
    throw new ImportersError('file_error', `读不了文件：${filePath}（${error.message}）`);
  }
}

function detectSource(where, fallback = 'text') {
  const value = String(where || '').toLowerCase();
  if (/(^|[\\/_-])qq|qq群|qq聊天/.test(value)) return 'qq';
  if (/wechat|weixin|微信/.test(value)) return 'wechat';
  return fallback;
}

function cleanSource(value, fallback) {
  const s = String(value || '').toLowerCase();
  return SOURCES.has(s) ? s : fallback;
}

function cleanKind(value, fallback) {
  const s = String(value || '').toLowerCase();
  return KINDS.has(s) ? s : fallback;
}

function parseDay(value) {
  if (value == null || value === '') return '';
  if (typeof value === 'number' && Number.isFinite(value)) {
    const ms = value > 1e12 ? value : value * 1000;
    return new Date(ms).toISOString().slice(0, 10);
  }
  const s = String(value).trim();
  const iso = s.match(/(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})/);
  if (iso) {
    const y = iso[1];
    const m = String(Number(iso[2])).padStart(2, '0');
    const d = String(Number(iso[3])).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }
  const parsed = new Date(s);
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString().slice(0, 10);
}

function makeItem(text, opts = {}) {
  const value = String(text == null ? '' : text).replace(/\r\n?/g, '\n').trim();
  if (!value) return null;
  const source = cleanSource(opts.source, 'text');
  const kind = cleanKind(opts.kind, source === 'qq' || source === 'wechat' ? 'chat' : 'line');
  const item = {
    text: value,
    source,
    where: String(opts.where || 'text'),
    kind,
    day: parseDay(opts.day) || '',
  };
  if (Number.isFinite(opts.likes)) item.likes = opts.likes;
  if (typeof opts.fish === 'boolean') item.fish = opts.fish;
  if (Array.isArray(opts.labels)) item.labels = opts.labels.slice();
  return item;
}

function parseCsvRows(text) {
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (ch === '"') {
        quoted = false;
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') {
      quoted = true;
    } else if (ch === ',') {
      row.push(field);
      field = '';
    } else if (ch === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else if (ch !== '\r') {
      field += ch;
    }
  }
  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((cell) => cell.trim() !== ''));
}

const TEXT_KEYS = ['text', 'content', 'message', 'msg', '消息', '内容', '正文', 'body'];
const TIME_KEYS = ['time', 'date', 'day', 'timestamp', '时间', '日期'];
const SENDER_KEYS = ['sender', 'name', 'user', 'nickname', 'from', '发送者', '昵称'];
const LIKE_KEYS = ['likes', 'like', '点赞', '赞'];

function fieldIndex(header, keys) {
  const lower = header.map((h) => String(h).trim().toLowerCase());
  for (const key of keys) {
    const index = lower.indexOf(key.toLowerCase());
    if (index >= 0) return index;
  }
  return -1;
}

function parseCsv(text, opts) {
  const rows = parseCsvRows(text);
  if (!rows.length) return [];
  const header = rows[0];
  const textIdx = fieldIndex(header, TEXT_KEYS);
  const hasHeader = textIdx >= 0;
  const dataRows = hasHeader ? rows.slice(1) : rows;
  const idx = hasHeader ? textIdx : 0;
  const timeIdx = hasHeader ? fieldIndex(header, TIME_KEYS) : -1;
  const senderIdx = hasHeader ? fieldIndex(header, SENDER_KEYS) : -1;
  const likeIdx = hasHeader ? fieldIndex(header, LIKE_KEYS) : -1;
  const items = [];
  for (const row of dataRows) {
    const text = row[idx];
    if (text == null || !String(text).trim()) continue;
    const likesValue = likeIdx >= 0 ? String(row[likeIdx] == null ? '' : row[likeIdx]).trim() : '';
    const likes = likesValue === '' ? undefined : Number(likesValue);
    const item = makeItem(text, {
      source: opts.source,
      where: opts.where,
      kind: senderIdx >= 0 || timeIdx >= 0 ? 'chat' : 'line',
      day: timeIdx >= 0 ? row[timeIdx] : opts.day,
      likes: Number.isFinite(likes) ? likes : undefined,
    });
    if (item) items.push(item);
  }
  return items;
}

function resolveArray(value) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== 'object') return [];
  for (const key of ['messages', 'items', 'data', 'list', 'records', 'lines']) {
    if (Array.isArray(value[key])) return value[key];
  }
  return [value];
}

function textOf(value) {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return '';
  for (const key of TEXT_KEYS) {
    if (typeof value[key] === 'string') return value[key];
  }
  if (value.content && typeof value.content === 'object') {
    for (const key of ['text', 'message', 'msg', '消息']) {
      if (typeof value.content[key] === 'string') return value.content[key];
    }
  }
  return '';
}

function parseJson(text, opts) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (error) {
    throw new ImportersError('json_error', `JSON 解析失败：${error.message}`);
  }
  const rows = resolveArray(data);
  const items = [];
  for (const row of rows) {
    const textValue = textOf(row);
    if (!String(textValue).trim()) continue;
    const source = row && typeof row === 'object' && row.source
      ? cleanSource(row.source, opts.source)
      : opts.source;
    const where = row && typeof row === 'object' && row.where ? row.where : opts.where;
    const kind = row && typeof row === 'object' && row.kind
      ? cleanKind(row.kind, source === 'qq' || source === 'wechat' ? 'chat' : 'line')
      : (source === 'qq' || source === 'wechat' ? 'chat' : 'line');
    const dayValue = row && typeof row === 'object'
      ? (row.day || row.time || row.date || row.timestamp)
      : opts.day;
    const likesValue = row && typeof row === 'object' ? (row.likes != null ? row.likes : row.like) : undefined;
    const likes = likesValue == null || String(likesValue).trim() === '' ? undefined : Number(likesValue);
    const item = makeItem(textValue, {
      source,
      where,
      kind,
      day: dayValue != null ? dayValue : opts.day,
      likes: Number.isFinite(likes) ? likes : undefined,
      fish: row && typeof row === 'object' ? row.fish : undefined,
      labels: row && typeof row === 'object' ? row.labels : undefined,
    });
    if (item) items.push(item);
  }
  return items;
}

const DATE_FIRST = /^(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?[ T]\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$/;
const NAME_FIRST = /^(.+?)\s+(\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)$/;

function headerOf(line) {
  const trimmed = line.trim();
  let match = trimmed.match(DATE_FIRST);
  if (match) return { day: match[1], sender: match[2].trim() };
  match = trimmed.match(NAME_FIRST);
  if (match) return { day: match[2], sender: match[1].trim() };
  return null;
}

function parseText(text, opts) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const items = [];
  const fallbackSource = opts.source;
  let current = null;

  const flush = () => {
    if (!current) return;
    const item = makeItem(current.text, {
      source: fallbackSource,
      where: opts.where,
      kind: 'chat',
      day: current.day,
    });
    if (item) items.push(item);
    current = null;
  };

  for (const line of lines) {
    const header = headerOf(line);
    if (header) {
      flush();
      current = { day: header.day, sender: header.sender, text: '' };
      continue;
    }
    if (current) {
      if (line.trim()) current.text += `${current.text ? '\n' : ''}${line}`;
    } else if (line.trim()) {
      const item = makeItem(line, {
        source: fallbackSource,
        where: opts.where,
        kind: 'line',
        day: opts.day,
      });
      if (item) items.push(item);
    }
  }
  flush();
  return items;
}

function detectFormat(text, where, opts) {
  if (opts.format) return String(opts.format).toLowerCase();
  const ext = path.extname(String(where || '')).toLowerCase();
  if (ext === '.json') return 'json';
  if (ext === '.csv') return 'csv';
  if (ext === '.txt') return 'text';
  const trimmed = text.trim();
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) return 'json';
  const first = trimmed.split(/\r?\n/, 1)[0] || '';
  if (first.includes(',') && /text|content|message|msg|消息|内容|正文/i.test(first)) return 'csv';
  return 'text';
}

/**
 * @param {string|Buffer} input 文件路径、文本或 Buffer
 * @param {object} [opts]
 * @param {boolean} [opts.text] true = 把 input 当文本
 * @param {boolean} [opts.stdin] true = 把 input 当 stdin 文本
 * @param {string} [opts.source] bili|qq|wechat|text
 * @param {string} [opts.where]
 * @param {string} [opts.format] json|csv|text
 * @param {string} [opts.kind]
 * @returns {Array}
 */
function parse(input, opts = {}) {
  let text;
  let where = opts.where;
  let source = opts.source;

  if (opts.text === true || opts.stdin === true || opts.fromText === true) {
    text = Buffer.isBuffer(input) ? decodeBuffer(input) : String(input ?? '');
    where = where || 'stdin';
  } else if (typeof input === 'string' && input !== '-' && fs.existsSync(input) && fs.statSync(input).isFile()) {
    text = readFileText(input);
    where = where || path.basename(input);
  } else {
    text = Buffer.isBuffer(input) ? decodeBuffer(input) : String(input ?? '');
    where = where || 'text';
  }

  if (!text.trim()) return [];
  where = String(where || 'text');
  source = source ? cleanSource(source, 'text') : detectSource(where, 'text');

  const format = detectFormat(text.trim(), where, opts);
  let items;
  if (format === 'json') {
    items = parseJson(text.trim(), { source, where, day: opts.day });
  } else if (format === 'csv') {
    items = parseCsv(text.trim(), { source, where, day: opts.day });
  } else {
    items = parseText(text, { source, where, day: opts.day });
  }

  if (opts.kind) {
    for (const item of items) item.kind = cleanKind(opts.kind, item.kind);
  }
  if (opts.day) {
    for (const item of items) if (!item.day) item.day = parseDay(opts.day);
  }
  return items;
}

module.exports = {
  parse,
  parseText,
  parseCsv,
  parseJson,
  parseDay,
  decodeBuffer,
  detectSource,
  ImportersError,
  _internal: { parseCsvRows, headerOf },
};
