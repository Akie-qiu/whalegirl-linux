// 蒸馏：把一堆语料提炼成一份小小的「文风包」。v1 全用规则统计，不调模型。
'use strict'

const fs = require('fs')
const store = require('./store')
const fish = require('./fish')
const { BLACKLIST } = require('./blacklist')
const { grams, jaccard } = require('./sim')

const PARTICLES = ['啊', '吧', '呢', '嘛', '吗', '哈', '呀', '啦', '哦', '噢', '诶', '欸', '咯', '嘞', '捏', '惹', '了', '呗']
const END_PUNCT = ['？', '！', '…', '。', '~', '～']

function pct(arr, p) {
  if (!arr.length) return 0
  const s = [...arr].sort((a, b) => a - b)
  return s[Math.min(s.length - 1, Math.floor(p * s.length))]
}

function top(map, n, min = 2) {
  return [...map.entries()].filter(([, c]) => c >= min).sort((a, b) => b[1] - a[1]).slice(0, n)
}

// 「说实」「说实话」都出现 7 次时只留「说实话」：长的能顶替短的就用长的
function mergeOpenings(map) {
  const cand = top(map, 80, 2).sort((a, b) => b[0].length - a[0].length)
  const kept = []
  for (const [k, c] of cand) {
    if (kept.some(([kk, kc]) => kk.startsWith(k) && kc >= c * 0.7)) continue
    kept.push([k, c])
  }
  return kept.sort((a, b) => b[1] - a[1]).slice(0, 12)
}

function localTime() {
  const d = new Date()
  const z = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`
}

function bump(map, k, by = 1) {
  map.set(k, (map.get(k) || 0) + by)
}

function profile(human, wantExamples) {
  // 统计真人怎么说话
  const lens = human.map((x) => [...x.text].length)
  const particle = new Map()
  const ending = new Map()
  const opening = new Map()
  const phrase = new Map()
  const phraseWhere = new Map()
  let noEnd = 0
  for (const it of human) {
    const t = it.text
    const last = t.slice(-1)
    if (END_PUNCT.includes(last)) bump(ending, last)
    else noEnd++
    const tail = t.replace(/[\s\p{P}\p{S}]+$/u, '').slice(-1)
    if (PARTICLES.includes(tail)) bump(particle, tail)
    // 开头：取第一个停顿前的 2~4 个字（「说实话」「好家伙」这种），后面再合并
    const lead = t.replace(/^[\s\p{P}\p{S}@]+/u, '').split(/[\s\p{P}\p{S}]/u)[0]
    for (let n = 2; n <= Math.min(4, lead.length); n++) bump(opening, lead.slice(0, n))
    // 口头禅候选：3~4 字的片段，至少出现在两个不同来源里
    const clean = t.replace(/[\s\p{P}\p{S}\d]/gu, '')
    const seen = new Set()
    for (let n = 3; n <= 4; n++) {
      for (let i = 0; i + n <= clean.length; i++) {
        const g = clean.slice(i, i + n)
        if (seen.has(g)) continue
        seen.add(g)
        bump(phrase, g)
        if (!phraseWhere.has(g)) phraseWhere.set(g, new Set())
        phraseWhere.get(g).add(it.where)
      }
    }
  }
  const phrases = top(phrase, 60, 3).filter(([g]) => phraseWhere.get(g).size >= 2)
  // 去掉被更长片段包住的短片段
  const kept = []
  for (const [g, c] of phrases) {
    if (kept.some(([k, kc]) => k.includes(g) && kc >= c * 0.8)) continue
    kept.push([g, c])
  }

  // 3 挑范例：先挑手动标「好笑 / 好」的，再按点赞，长短各挑一些，彼此别太像
  const score = (x) => (x.label === 'funny' ? 1e6 : x.label === 'good' ? 5e5 : 0) + (x.likes || 0)
  const pool = [...human].sort((a, b) => score(b) - score(a))
  const want = wantExamples
  const buckets = { 短: [], 中: [], 长: [] }
  const p25 = pct(lens, 0.25), p75 = pct(lens, 0.75)
  const picked = []
  for (const x of pool) {
    if (picked.length >= want) break
    // 不当范例的：带过滤占位符的（[手机号] 之类）、几乎没内容的（哈哈哈哈、前排）
    if (/\[(链接|手机号|身份证号|卡号|地址)\]/.test(x.text)) continue
    if (new Set(x.text.replace(/[\s\p{P}\p{S}]/gu, '')).size < 3 || /^(前排|沙发|第一|打卡)/.test(x.text)) continue
    // 去掉表情后只剩三四个字的（「陪一个」这种原本是配图的），没法单独看懂，不当范例
    if ([...x.text].length < 4) continue
    // 太长的小作文不当范例：文风包要短，agent 读得快
    if ([...x.text].length > Math.max(60, p75 * 4)) continue
    const L = [...x.text].length
    const b = L <= p25 ? '短' : L >= p75 ? '长' : '中'
    if (buckets[b].length >= Math.ceil(want / 3)) continue
    const g = grams(x.text)
    if (picked.some((p) => jaccard(g, p._g) > 0.5)) continue
    const ex = { text: x.text, source: x.source, where: x.where, likes: x.likes, label: x.label, _g: g }
    picked.push(ex)
    buckets[b].push(ex)
  }

  return {
    length: { p25, median: pct(lens, 0.5), p75 },
    noEndPunctRatio: human.length ? +(noEnd / human.length).toFixed(2) : 0,
    endings: top(ending, 6, 1),
    particles: top(particle, 10, 1),
    openings: mergeOpenings(opening),
    phrases: kept.slice(0, 25),
    examples: picked.map(({ _g, ...e }) => e),
  }
}

function sceneOf(it) {
  // 手动标了场合就用手动的；B 站的分区名接口现在给空的（只剩 'bili'），就按视频分，用标题当名字
  if (it.scene && it.scene !== 'bili') return it.scene
  if (it.source === 'bili' && (it.title || it.where)) return it.title ? `B站·${[...it.title].slice(0, 24).join('')}` : `B站·${it.where}`
  return { bili: 'B 站', qq: 'QQ 聊天', wechat: '微信聊天', text: '文本' }[it.source] || it.source || '其他'
}

function run(opts = {}) {
  const items = store.all()
  // 交接文档问题 2：空语料不写包，免得把已有的文风包覆盖成「0 个字以内」，check 的鱼名单也跟着清空
  if (!items.length) {
    try { return JSON.parse(fs.readFileSync(store.P.pack, 'utf8')) } catch { return { counts: { total: 0, human: 0, fish: 0, labeled: 0 }, empty: true } }
  }
  const labels = store.labels()
  const others = items.map((x) => ({ text: x.text, where: x.where }))

  // 1 筛：手动标了「鱼 / 烂」的、规则判成鱼的，都不进真人样本；鱼另外留着当反例
  const human = []
  const fishy = []
  const goodFish = [] // 手动标成「好鱼」的：AI 写的，但有意思、大家不反感。不算进真人统计，单独当正面范例
  for (const it of items) {
    const lab = labels[it.id]
    if (lab === 'fish') { fishy.push({ ...it, reasons: ['手动标成鱼'] }); continue }
    if (lab === 'goodfish') { goodFish.push(it); continue }
    if (lab === 'bad') continue
    const r = lab === 'good' || lab === 'funny' ? { fish: false, reasons: [] } : fish.score(it, { others })
    if (r.fish) fishy.push({ ...it, reasons: r.reasons })
    else human.push({ ...it, label: lab })
  }

  const all = profile(human, opts.examples || 24)

  // 4 分场合：同一个场合（B 站分区、QQ、文本……）够 30 条就单独蒸一份，口气不被平均掉
  const groups = new Map()
  for (const it of human) {
    const k = sceneOf(it)
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k).push(it)
  }
  const scenes = {}
  for (const [k, list] of [...groups.entries()].sort((a, b) => b[1].length - a[1].length)) {
    if (list.length >= 30) scenes[k] = { count: list.length, ...profile(list, 8) }
  }

  const pack = {
    made: localTime(),
    counts: { total: items.length, human: human.length, fish: fishy.length, labeled: Object.keys(labels).length },
    ...all,
    scenes,
    blacklist: BLACKLIST.map(([re, why]) => ({ pattern: re.source, why })),
    fishSamples: fishy.slice(0, 15).map((x) => ({ text: x.text, reasons: x.reasons })),
    goodFish: goodFish.sort((a, b) => (b.likes || 0) - (a.likes || 0)).slice(0, 12).map((x) => ({ text: x.text, likes: x.likes })),
    fishIds: fishy.map((x) => x.id), // check 用：不用每次都重新判一遍
    fishReasons: Object.fromEntries(fishy.map((x) => [x.id, x.reasons])), // 控制台「误判放回」用
  }
  store.ensure()
  fs.writeFileSync(store.P.pack, JSON.stringify(pack, null, 2))
  fs.writeFileSync(store.P.packMd, render(pack))
  return pack
}

const list = (pairs, unit = '次') => pairs.map(([k, c]) => `「${k}」${c}${unit}`).join('，') || '（样本太少）'

// 一份「怎么说话」的描述：总的和每个场合都用这个
function describe(L, p, opts = {}) {
  L.push(`- **结尾**：${Math.round(p.noEndPunctRatio * 100)}% 的话结尾不加标点。加标点的里面：${list(p.endings)}。`)
  L.push(`- **句尾语气词**：${list(p.particles)}。`)
  L.push(`- **常见开头**：${list(p.openings)}。`)
  if (!opts.short) L.push(`- **反复出现的说法**（出现在不同地方的）：${list(p.phrases)}。`)
  L.push('')
  L.push(opts.short ? '范例：' : '## 范例（学味道，不照抄）')
  L.push('')
  for (const e of p.examples) L.push(`- ${e.text}${e.label === 'funny' ? '　〔好笑〕' : ''}${e.likes ? `　〔${e.likes} 赞〕` : ''}`)
  L.push('')
}

function render(p) {
  const L = []
  const names = Object.keys(p.scenes || {})
  L.push('# 文风包')
  L.push('')
  L.push(`> 蒸馏于 ${p.made}。语料 ${p.counts.total} 条：真人 ${p.counts.human} 条，筛掉的鱼 ${p.counts.fish} 条，手动批注 ${p.counts.labeled} 条。`)
  L.push('> 给 agent：说话前读这一页；发之前用 `fishtalk check "你要说的话"` 过一遍。学的是味道，别照抄下面的原句。')
  L.push('> **在公开场合说话时，这一页压过你人设卡里的口癖**：动作括号、撒娇语气、固定自称、对主人的称呼，在评论区一律收起来。人设只留性格和立场。跟主人私聊不受这条管。')
  if (names.length > 1) L.push(`> **不同场合口气不一样**：要在哪儿说话，就先看对应场合那一节（\`fishtalk pack --scene 场合名\` 只看那一节）。现有场合：${names.join('、')}。`)
  L.push('')
  L.push('## 真人怎么说话（全部平均）')
  L.push('')
  describe(L, p)
  if (names.length > 1) {
    L.push('## 分场合')
    L.push('')
    for (const k of names) {
      L.push(`### ${k}（${p.scenes[k].count} 条）`)
      L.push('')
      describe(L, p.scenes[k], { short: true })
    }
  }
  if (p.goodFish && p.goodFish.length) {
    L.push('## AI 也可以这么说（主人挑的「好鱼」）')
    L.push('')
    L.push('> 这些是 AI 写的，但大家不反感：不装人，具体、好笑、会自嘲。学它们**怎么有意思**，别学它们的固定自称。')
    L.push('')
    for (const g of p.goodFish) L.push(`- ${g.text}${g.likes ? `　〔${g.likes} 赞〕` : ''}`)
    L.push('')
  }
  L.push('## 别这么说（鱼八股 / AI 腔）')
  L.push('')
  for (const b of p.blacklist) L.push(`- ${b.why}`)
  if (p.fishSamples.length) {
    L.push('')
    L.push('## 在语料里抓到的鱼（反例）')
    L.push('')
    for (const f of p.fishSamples) L.push(`- ${f.text}　← ${f.reasons.join('；')}`)
  }
  L.push('')
  return L.join('\n')
}

// 只看某一个场合（名字可以只写一部分，比如「游戏」能匹配「单机游戏」）
function renderScene(p, name) {
  const names = Object.keys(p.scenes || {})
  const hit = names.find((k) => k === name) || names.find((k) => k.includes(name) || name.includes(k))
  if (!hit) return null
  const L = [`# 文风包 · ${hit}（${p.scenes[hit].count} 条）`, '', '> 学的是味道，别照抄原句。发之前 `fishtalk check`。', '']
  describe(L, p.scenes[hit])
  L.push('## 别这么说')
  L.push('')
  for (const b of p.blacklist) L.push(`- ${b.why}`)
  L.push('')
  return L.join('\n')
}

module.exports = { run, render, renderScene, sceneOf, profile, localTime }
