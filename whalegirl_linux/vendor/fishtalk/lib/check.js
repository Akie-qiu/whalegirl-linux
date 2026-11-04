// check：一句话发出去之前过一遍。只指出问题，不替你改。
'use strict'

const fs = require('fs')
const store = require('./store')
const fish = require('./fish')
const { nearest, lcs, norm } = require('./sim')

// 最长公共子串里有几个汉字（跟 sim.lcs 用同一套归一化）
function cjkInCommon(a, b) {
  const x = norm(a), y = norm(b)
  let best = 0, end = 0
  const prev = new Array(y.length + 1).fill(0)
  for (let i = 1; i <= x.length; i++) {
    let diag = 0
    for (let j = 1; j <= y.length; j++) {
      const tmp = prev[j]
      prev[j] = x[i - 1] === y[j - 1] ? diag + 1 : 0
      if (prev[j] > best) { best = prev[j]; end = i }
      diag = tmp
    }
  }
  return (x.slice(end - best, end).match(/[一-鿿]/g) || []).length
}
const { hits } = require('./blacklist')

// fishrules 的规则代号 → 给人看的说明
const SOFT_NAMES = {
  'not-a-but-b': '「不是 A，而是 B」式对仗',
  root: '「说到根上了」',
  itself: '「这本身 / 本质上 / 归根结底」总结腔',
  contrast: '「与其…不如 / 看似…实则 / 不仅…而且」作文腔',
  summary: '开头就总结（总之、所以说、由此可见）',
  'self-fish': '自称「本鱼 / 鱼鱼」',
  'fish-meme': '鱼梗自称',
  'self-as-fish': '「作为一条鱼 / 一个 AI」',
  owner: '跟主人撒娇式的话',
  finished: '「已阅 / 看完了」打卡腔',
  'as-ai': '「作为一个 AI」',
  report: '报告腔、客服腔',
  'work-cliche': '套话收尾，像鱼',
}
const nice = (s) => SOFT_NAMES[s] || s

function loadPack() {
  try { return JSON.parse(fs.readFileSync(store.P.pack, 'utf8')) } catch { return null }
}

function run(text, opts = {}) {
  const problems = []
  const notes = []
  const t = String(text || '').trim()
  if (!t) return { ok: false, problems: ['没有内容'], notes }

  const items = store.all()
  const labels = store.labels()
  const pack = loadPack()
  const direct = fish.score(t, {
    forCheck: true,
    others: items.map((x) => ({ text: x.text, where: x.where })),
  })
  for (const h of direct.hard) problems.push(`鱼八股原句：「${h.replace(/^hard:/, '')}」`)
  for (const s of direct.soft) notes.push(`有 AI 腔：${nice(s.replace(/^soft:/, ''))}`)
  for (const r of direct.repeats) notes.push(`重复：${r.replace(/^repeat:/, '')}`)
  if (direct.fish && direct.hard.length === 0) problems.push('AI 腔叠了好几处，太像鱼了')
  // 内置黑名单里 fishrules 没覆盖到的（比如人设动作括号），也直接拦
  for (const why of hits(t)) {
    const dup = direct.hard.some((h) => why.includes(h.replace(/^hard:/, '')))
    if (!dup) problems.push(`撞黑名单：${why}`)
  }

  // 有文风包就用蒸馏时判好的鱼名单；没有才现场判（慢一点）
  const known = pack && pack.fishIds ? new Set(pack.fishIds) : null
  const others = known ? null : items.map((x) => ({ text: x.text, where: x.where }))
  const fishPool = []
  const humanPool = []
  for (const it of items) {
    const lab = labels[it.id]
    if (lab === 'goodfish') { humanPool.push(it); continue } // 好鱼：不当反例；但也不许照抄（下面的照抄检查会查它）
    const isFish = lab === 'fish' || (lab !== 'good' && lab !== 'funny' && (known ? known.has(it.id) : fish.score(it, { others }).fish))
    if (isFish) fishPool.push(it)
    else humanPool.push(it)
  }

  const nf = nearest(t, fishPool)
  if (nf.score >= 0.5) problems.push(`跟抓到的鱼评太像（${Math.round(nf.score * 100)}%）：「${nf.text}」`)

  const ns = opts.ignoreHistory ? { score: 0 } : nearest(t, store.said())
  if (ns.score >= 0.6) problems.push(`跟你以前说过的话太像（${Math.round(ns.score * 100)}%）：「${ns.text}」`)

  if (!opts.allowCopy) {
    for (const h of humanPool) {
      const n = lcs(t, h.text)
      // 重合的只是品牌名、数字（「DeepSeek」「百万token」）不算照抄：重合部分至少要有 4 个汉字
      if (n >= 6 && cjkInCommon(t, h.text) < 4) continue
      if (n >= 10 || (n >= 6 && n >= [...h.text].length * 0.8)) {
        problems.push(`照抄了语料原句（连续 ${n} 个字一样）：「${h.text}」`)
        break
      }
    }
  }

  if (!(pack && pack.length && pack.length.p75)) {
    notes.push('还没有文风包，先跑一次 `fishtalk distill` 检查会更准')
  }

  const ok = problems.length === 0
  if (ok && opts.keep) store.addSaid(t)
  return { ok, problems, notes }
}

// 把 fishrules 的理由代号（hard:… / soft:… / repeat:…）翻成人话，控制台也用
const REPEAT_NAMES = { known_fish: '跟已知的鱼评很像', own: '跟自己以前说的很像', corpus: '在语料里反复出现', other: '跟别处的评论几乎一样', known: '跟已知的鱼评很像' }
function explain(reason) {
  const r = String(reason)
  if (r.startsWith('hard:')) return `鱼八股原句「${r.slice(5)}」`
  if (r.startsWith('soft:')) return `AI 腔：${nice(r.slice(5))}`
  if (r.startsWith('repeat:')) return REPEAT_NAMES[r.slice(7)] || '重复'
  return r
}

module.exports = { run, explain }
