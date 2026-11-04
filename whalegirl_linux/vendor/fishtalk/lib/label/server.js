// 鱼塘控制台：本地起一个只听 127.0.0.1 的小服务器，自动打开浏览器。关掉命令行窗口就停了。
// 页面在 page.html；这里只提供数据接口。
'use strict'

const http = require('http')
const fs = require('fs')
const path = require('path')
const { exec } = require('child_process')
const store = require('../store')

const PAGE = path.join(__dirname, 'page.html')
const OK_LABELS = new Set(['good', 'funny', 'goodfish', 'fish', 'bad'])

let sinceDistill = 0

function distillNow() {
  try { require('../distill').run(); return true } catch { return false }
}

function loadPack() {
  try { return JSON.parse(fs.readFileSync(store.P.pack, 'utf8')) } catch { return null }
}

// 把每条语料补上：属于哪个包（场合）、手动标记、自动判定
function enrich() {
  const { sceneOf } = require('../distill')
  const labels = store.labels()
  const pack = loadPack()
  const fishReasons = (pack && pack.fishReasons) || {}
  const { explain } = require('../check')
  return store.all().map((x) => {
    const label = labels[x.id] || null
    const autoFish = !label && Boolean(fishReasons[x.id])
    return {
      id: x.id, text: x.text, source: x.source, where: x.where, title: x.title, kind: x.kind, likes: x.likes || 0, day: x.day,
      pack: sceneOf(x), label, autoFish, reasons: (fishReasons[x.id] || []).map(explain),
    }
  })
}

function packs(items) {
  const map = new Map()
  for (const it of items) {
    if (!map.has(it.pack)) map.set(it.pack, { key: it.pack, total: 0, good: 0, funny: 0, goodfish: 0, fish: 0, bad: 0, autoFish: 0, unlabeled: 0, source: it.source, where: it.where })
    const p = map.get(it.pack)
    p.total++
    if (it.label) p[it.label]++
    else { p.unlabeled++; if (it.autoFish) p.autoFish++ }
  }
  return [...map.values()].sort((a, b) => b.total - a.total)
}

// 筛选：pack=包名 q=关键词 filter=all|unlabeled|labeled|good|funny|fish|bad|autofish
function query(items, url) {
  const pack = url.searchParams.get('pack') || ''
  const q = (url.searchParams.get('q') || '').trim().toLowerCase()
  const filter = url.searchParams.get('filter') || 'unlabeled'
  let list = items
  if (pack) list = list.filter((x) => x.pack === pack)
  if (q) list = list.filter((x) => x.text.toLowerCase().includes(q))
  if (filter === 'unlabeled') list = list.filter((x) => !x.label)
  else if (filter === 'labeled') list = list.filter((x) => x.label)
  else if (filter === 'autofish') list = list.filter((x) => x.autoFish)
  else if (OK_LABELS.has(filter)) list = list.filter((x) => x.label === filter)
  return list.sort((a, b) => b.likes - a.likes)
}

function overview(items) {
  const count = { good: 0, funny: 0, goodfish: 0, fish: 0, bad: 0 }
  let autoFish = 0
  for (const x of items) { if (x.label) count[x.label]++; else if (x.autoFish) autoFish++ }
  const labeled = count.good + count.funny + count.goodfish + count.fish + count.bad
  return { total: items.length, labeled, count, autoFish, auto: items.length - labeled }
}

function blacklist() {
  const out = require('../blacklist').BLACKLIST.map(([re, why]) => ({ kind: '内置', pattern: re.source, why }))
  try {
    const src = fs.readFileSync(path.join(__dirname, '..', 'fishrules.js'), 'utf8')
    for (const m of src.matchAll(/\{\s*code:\s*'([^']+)',\s*re:\s*(\/(?:\\.|[^/\n])+\/[a-z]*)/g)) out.push({ kind: '软信号', pattern: m[2], why: m[1] })
  } catch { /* 没有 fishrules 就只列内置 */ }
  return out
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', (c) => { body += c; if (body.length > 2e6) req.destroy() })
    req.on('end', () => { try { resolve(JSON.parse(body || '{}')) } catch (e) { reject(e) } })
  })
}

function send(res, code, body, type = 'application/json; charset=utf-8') {
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store' })
  res.end(typeof body === 'string' ? body : JSON.stringify(body))
}

function openBrowser(url) {
  const cmd = process.platform === 'win32' ? `start "" "${url}"` : process.platform === 'darwin' ? `open "${url}"` : `xdg-open "${url}"`
  exec(cmd, () => {})
}

async function route(req, res) {
  const url = new URL(req.url, 'http://x')
  const p = url.pathname
  if (req.method === 'GET' && p === '/') return send(res, 200, fs.readFileSync(PAGE, 'utf8'), 'text/html; charset=utf-8')

  if (req.method === 'GET' && p === '/api/state') {
    const items = enrich()
    return send(res, 200, { overview: overview(items), packs: packs(items), home: store.P.home })
  }
  if (req.method === 'GET' && p === '/api/items') {
    const list = query(enrich(), url)
    const limit = Math.min(500, Number(url.searchParams.get('limit')) || 300)
    return send(res, 200, { total: list.length, items: list.slice(0, limit) })
  }
  if (req.method === 'GET' && p === '/api/blacklist') return send(res, 200, { rules: blacklist() })
  if (req.method === 'GET' && p === '/api/pack') {
    const pack = loadPack()
    if (!pack) return send(res, 200, { pack: null })
    const key = url.searchParams.get('pack')
    const sect = key && pack.scenes && pack.scenes[key] ? pack.scenes[key] : pack
    return send(res, 200, { pack: { made: pack.made, name: key || '全部', counts: pack.counts, length: sect.length, noEndPunctRatio: sect.noEndPunctRatio, endings: sect.endings, particles: sect.particles, openings: sect.openings, phrases: sect.phrases || [], examples: sect.examples, small: Boolean(key && !(pack.scenes && pack.scenes[key])) } })
  }

  if (req.method === 'POST' && p === '/api/label') {
    const { id, label } = await readBody(req)
    if (!id || (label && !OK_LABELS.has(label))) return send(res, 400, { error: 'bad label' })
    store.setLabel(id, label || null)
    let distilled = false
    if (label && ++sinceDistill >= 20) { sinceDistill = 0; distilled = distillNow() }
    return send(res, 200, { ok: true, distilled })
  }
  if (req.method === 'POST' && p === '/api/distill') return send(res, 200, { ok: distillNow() })

  // 语料包重命名：把这个包里每条的 scene 改成新名字（新名字跟别的包重名就等于合并）
  if (req.method === 'POST' && p === '/api/pack/rename') {
    const { from, to } = await readBody(req)
    const name = String(to || '').trim()
    if (!from || !name) return send(res, 400, { error: '名字不能为空' })
    const { sceneOf } = require('../distill')
    let n = 0
    store.rewrite((it) => (sceneOf(it) === from ? (n++, { ...it, scene: name }) : it))
    distillNow()
    return send(res, 200, { ok: true, renamed: n })
  }
  // 语料包删除：删掉这个包里的所有语料（连同批注），放进 trash.jsonl，误删还能找回
  if (req.method === 'POST' && p === '/api/pack/delete') {
    const { key } = await readBody(req)
    if (!key) return send(res, 400, { error: 'no key' })
    const { sceneOf } = require('../distill')
    const gone = new Set()
    const r = store.rewrite((it) => (sceneOf(it) === key ? (gone.add(it.id), null) : it))
    const labels = store.labels()
    for (const id of gone) if (labels[id]) store.setLabel(id, null)
    distillNow()
    return send(res, 200, { ok: true, deleted: r.trashed })
  }
  if (req.method === 'POST' && p === '/api/feed') {
    const { text, scene } = await readBody(req)
    const importers = require('../importers')
    let items = importers.parse(String(text || ''), { fromText: true, source: 'text', where: scene || '控制台' })
    if (scene) items = items.map((x) => ({ ...x, scene }))
    const added = store.add(items)
    const distilled = added ? distillNow() : false
    return send(res, 200, { read: items.length, added, distilled })
  }
  send(res, 404, { error: 'not found' })
}

function start(opts = {}) {
  const server = http.createServer((req, res) => {
    route(req, res).catch((e) => send(res, 500, { error: e.message }))
  })
  server.listen(opts.port || 0, '127.0.0.1', () => {
    const url = `http://127.0.0.1:${server.address().port}/`
    console.log(`鱼塘控制台已打开：${url}`)
    console.log('用完直接关掉这个窗口就行（或者按 Ctrl+C）。')
    if (!opts.noOpen) openBrowser(url)
  })
  return server
}

module.exports = { start }
