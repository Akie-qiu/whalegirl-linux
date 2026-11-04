'use strict';

/**
 * 贴吧自动学习适配器：
 *   discover("原神", {limit}) -> 帖子链接[]
 *   fetch(link) -> Item[]
 *
 * 不登录、只读；串行 2.5 秒/请求。贴吧当前对无登录请求返
 * 403「百度安全验证」——这里不绕验证，直接抛 blocked，027 里照实记。
 * 如果用户自己有可用 cookie，可把 HTML 传给 parseThreads/parsePosts。
 */

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const INTERVAL = 2500;
const BLOCK_MS = 10 * 60 * 1000;

let lastRequestAt = 0;
let blockedUntil = 0;

class TiebaError extends Error {
  constructor(code, message, detail = {}) {
    super(message);
    this.name = 'TiebaError';
    this.code = code;
    this.detail = detail;
  }
}

function resetRateLimit() {
  lastRequestAt = 0;
  blockedUntil = 0;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function wait(opts = {}) {
  const now = Date.now();
  if (now < blockedUntil) {
    const retryAfter = Math.max(1, Math.ceil((blockedUntil - now) / 1000));
    throw new TiebaError('rate_limited', `贴吧风控冷却中，约 ${retryAfter} 秒后再试。`, { retryAfter });
  }
  const interval = Number.isFinite(opts.intervalMs) ? Math.max(0, opts.intervalMs) : INTERVAL;
  const gap = Math.max(0, lastRequestAt + interval - now);
  if (gap > 0) await (opts.sleep || sleep)(gap);
  lastRequestAt = Date.now();
}

function block(message) {
  blockedUntil = Date.now() + BLOCK_MS;
  throw new TiebaError('blocked', message, { retryAfter: Math.ceil(BLOCK_MS / 1000) });
}

function looksBlocked(html) {
  return /百度安全验证|安全验证|验证码|wappass|访问过于频繁|请稍后重试|异常访问/.test(html);
}

async function request(url, opts = {}) {
  await wait(opts);
  const fetchImpl = opts.fetch || globalThis.fetch;
  if (typeof fetchImpl !== 'function') throw new TiebaError('no_fetch', '需要 Node 18 的 fetch。');
  const res = await fetchImpl(url, {
    headers: {
      'user-agent': opts.userAgent || UA,
      referer: opts.referer || 'https://tieba.baidu.com/',
      accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'accept-language': 'zh-CN,zh;q=0.9',
      ...(opts.headers || {}),
    },
    signal: typeof AbortSignal !== 'undefined' && AbortSignal.timeout
      ? AbortSignal.timeout(opts.timeoutMs || 15000)
      : undefined,
  });
  const html = await res.text();
  if (res.status === 403 || res.status === 412 || looksBlocked(html)) {
    block('贴吧风控：403/百度安全验证，未登录拿不到帖子。');
  }
  if (!res.ok) throw new TiebaError('http_error', `贴吧返回 HTTP ${res.status}`, { status: res.status });
  return { html, url: res.url || url };
}

function decodeHtml(text) {
  return String(text)
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<img\b[^>]*>/gi, '[图]')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)))
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function parseThreads(html) {
  const threads = [];
  const seen = new Set();
  const re = /data-field=(['"])([\s\S]*?)\1/g;
  let match;
  while ((match = re.exec(html))) {
    let raw = match[2];
    if (match[1] === '"') raw = raw.replace(/&quot;/g, '"');
    raw = raw.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
    let data;
    try {
      data = JSON.parse(raw);
    } catch (error) {
      continue;
    }
    const tid = Number(data.id || data.tid);
    if (!Number.isInteger(tid) || tid <= 0 || seen.has(tid)) continue;
    const reply = Number(data.reply_num);
    if (!Number.isFinite(reply)) continue;
    seen.add(tid);
    threads.push({ tid, reply, title: data.title || '' });
  }
  // 没有 data-field 时，退到最朴素的 /p/ 链接（无法知道回复数，按出现顺序）
  if (!threads.length) {
    const linkRe = /href="\/p\/(\d+)[^"]*"[^>]*>([\s\S]*?)<\/a>/g;
    while ((match = linkRe.exec(html))) {
      const tid = Number(match[1]);
      if (!tid || seen.has(tid)) continue;
      seen.add(tid);
      threads.push({ tid, reply: 0, title: decodeHtml(match[2]) });
    }
  }
  return threads.sort((a, b) => b.reply - a.reply);
}

function parsePosts(html, link) {
  const items = [];
  const re = /<div\b[^>]*class="[^"]*\bd_post_content\b[^"]*"[^>]*>([\s\S]*?)<\/div>/gi;
  let match;
  while ((match = re.exec(html))) {
    const text = decodeHtml(match[1]);
    if (!text || text.length < 2) continue;
    if (/广告|该楼层疑似违规|百度贴吧客户端/.test(text)) continue;
    items.push({
      text,
      source: 'text',
      where: link,
      kind: 'comment',
      day: '',
    });
  }
  return items;
}

function clampLimit(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(1, Math.min(20, Math.floor(n))) : 10;
}

async function discover(target, opts = {}) {
  const kw = String(target || '').trim();
  if (!kw) throw new TiebaError('invalid_input', '吧名不能为空。');
  const limit = clampLimit(opts.limit);
  const url = `https://tieba.baidu.com/f?kw=${encodeURIComponent(kw)}&ie=utf-8&pn=0`;
  const { html } = await request(url, opts);
  const threads = parseThreads(html);
  if (!threads.length) {
    throw new TiebaError('no_results', `贴吧「${kw}」没有解析到帖子（可能需要登录/验证）。`);
  }
  return threads.slice(0, limit).map((x) => `https://tieba.baidu.com/p/${x.tid}?pn=1`);
}

async function fetchOne(link, opts = {}) {
  const url = String(link || '').trim();
  if (!/^https?:\/\/tieba\.baidu\.com\/p\/\d+/.test(url)) {
    throw new TiebaError('invalid_input', `不是贴吧帖子链接：${url}`);
  }
  const { html } = await request(url, opts);
  const items = parsePosts(html, url);
  if (!items.length) {
    throw new TiebaError('no_results', '帖子第一页没有解析到发言（可能需要登录/验证）。');
  }
  return items;
}

module.exports = {
  discover,
  fetch: fetchOne,
  TiebaError,
  resetRateLimit,
  parseThreads,
  parsePosts,
  _internal: { request, decodeHtml, looksBlocked },
};
