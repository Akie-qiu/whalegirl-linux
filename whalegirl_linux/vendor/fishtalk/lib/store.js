// 本地存储：所有东西都在 ~/.fishtalk/（可用环境变量 FISHTALK_HOME 改）。
// corpus.jsonl  语料，一行一条
// labels.json   手动批注 { id: "good|fish|bad|funny" }
// said.jsonl    自己发出去过的话（check --keep 记的），用来查「别老说一样的」
// pack.json / 文风包.md   蒸馏结果
'use strict'

const fs = require('fs')
const os = require('os')
const path = require('path')
const crypto = require('crypto')

// 默认放在用户目录；但像 DSH 这种沙箱只能写工作区，写不进去就退回当前目录下的 .fishtalk
function pickHome() {
  if (process.env.FISHTALK_HOME) return process.env.FISHTALK_HOME
  // 有一版误改名叫 flashtalk，用过那版的人数据在 ~/.flashtalk：新目录没有、旧的在，就继续用旧的
  const misnamed = path.join(os.homedir(), '.flashtalk')
  if (!fs.existsSync(path.join(os.homedir(), '.fishtalk')) && fs.existsSync(misnamed)) return misnamed
  const home = path.join(os.homedir(), '.fishtalk')
  try {
    fs.mkdirSync(home, { recursive: true })
    const probe = path.join(home, '.probe')
    fs.writeFileSync(probe, '')
    fs.unlinkSync(probe)
    return home
  } catch {
    return path.join(process.cwd(), '.fishtalk')
  }
}
const HOME = pickHome()
const P = {
  home: HOME,
  corpus: path.join(HOME, 'corpus.jsonl'),
  labels: path.join(HOME, 'labels.json'),
  said: path.join(HOME, 'said.jsonl'),
  pack: path.join(HOME, 'pack.json'),
  packMd: path.join(HOME, '文风包.md'),
}

function ensure() {
  fs.mkdirSync(HOME, { recursive: true })
}

// 只过滤真正敏感的信息（主人定的：不做人名匿名化）
const SENSITIVE = [
  [/https?:\/\/\S+/gi, '[链接]'],
  [/\b\d{17}[\dXx]\b/g, '[身份证号]'],
  [/(?<!\d)1[3-9]\d{9}(?!\d)/g, '[手机号]'],
  [/(?<!\d)\d{16,19}(?!\d)/g, '[卡号]'],
  [/[一-龥]{2,6}(省|市)[一-龥]{0,8}(区|县)[一-龥\d]{0,12}(路|街|道|巷)[一-龥\d]{0,8}\d+号/g, '[地址]'],
]
function scrub(text) {
  // 楼中楼开头的「回复 @某人 :」不是说话内容，去掉
  let t = String(text || '').replace(/^\s*回复\s*@[^\s:：]{1,30}\s*[:：]\s*/, '')
  for (const [re, rep] of SENSITIVE) t = t.replace(re, rep)
  // 表情不学（主人定）：B 站的 [doge][笑哭] 这类方括号表情、emoji 都去掉
  // 充电专属表情名字很长：[UPOWER_423895_扬唇一笑]
  t = t.replace(/\[UPOWER_[^\]\s\[]{1,40}\]/g, '')
  t = t.replace(/\[[^\]\s\[]{1,12}\]/g, (m) => (/^\[(链接|手机号|身份证号|卡号|地址)\]$/.test(m) ? m : ''))
  t = t.replace(/[\p{Extended_Pictographic}\u{1F3FB}-\u{1F3FF}‍️]/gu, '')
  return t.replace(/[ \t]{2,}/g, ' ').trim()
}

function idOf(item) {
  return crypto.createHash('sha1').update(`${item.source}|${item.where}|${item.text}`).digest('hex').slice(0, 12)
}

function readJsonl(file) {
  if (!fs.existsSync(file)) return []
  const out = []
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (!line.trim()) continue
    try { out.push(JSON.parse(line)) } catch { /* 坏行跳过 */ }
  }
  return out
}

function all() {
  return readJsonl(P.corpus)
}

// 追加语料：过滤敏感信息、去重、补 id 和日期。返回真正新增的条数。
function add(items) {
  ensure()
  const seen = new Set(all().map((x) => x.id))
  const today = new Date().toISOString().slice(0, 10)
  const lines = []
  for (const raw of items) {
    const text = scrub(raw.text)
    if (!text || text.length < 2) continue
    const it = { ...raw, text, day: raw.day || today }
    it.id = idOf(it)
    if (seen.has(it.id)) continue
    seen.add(it.id)
    lines.push(JSON.stringify(it))
  }
  if (lines.length) fs.appendFileSync(P.corpus, lines.join('\n') + '\n')
  return lines.length
}

function labels() {
  if (!fs.existsSync(P.labels)) return {}
  try { return JSON.parse(fs.readFileSync(P.labels, 'utf8')) } catch { return {} }
}
function setLabel(id, label) {
  ensure()
  const l = labels()
  if (label) l[id] = label
  else delete l[id]
  fs.writeFileSync(P.labels, JSON.stringify(l))
  return l
}

function said() {
  return readJsonl(P.said)
}
function addSaid(text) {
  ensure()
  fs.appendFileSync(P.said, JSON.stringify({ text, day: new Date().toISOString().slice(0, 10) }) + '\n')
}

// 整体改写语料（重命名、删除语料包用）。fn 返回 null 表示删掉这条；删掉的放进 trash.jsonl，误删还能找回
function rewrite(fn) {
  ensure()
  const kept = []
  const trashed = []
  for (const it of all()) {
    const out = fn(it)
    if (out) kept.push(JSON.stringify(out))
    else trashed.push(JSON.stringify({ ...it, deletedAt: new Date().toISOString() }))
  }
  fs.writeFileSync(P.corpus, kept.length ? kept.join('\n') + '\n' : '')
  if (trashed.length) fs.appendFileSync(path.join(HOME, 'trash.jsonl'), trashed.join('\n') + '\n')
  return { kept: kept.length, trashed: trashed.length }
}

function forget() {
  if (fs.existsSync(HOME)) fs.rmSync(HOME, { recursive: true, force: true })
}

module.exports = { P, ensure, scrub, all, add, labels, setLabel, said, addSaid, forget, readJsonl, rewrite }
