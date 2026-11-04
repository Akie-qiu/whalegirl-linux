// 0.3：比统计更深的两样东西，工具本身仍然不调模型。
// like：按话题/要回的那条评论，找语料里最像的几条真人发言当现场范例。
// 打法：工具挑一批有代表性的语料 + 一份固定提纲，交给 agent 自己读、自己写一页《打法》，存回来，pack 时一起读。
'use strict'

const fs = require('fs')
const path = require('path')
const store = require('./store')
const { grams, norm, lcs } = require('./sim')

const PLAYBOOKS = path.join(store.P.home, 'playbooks.json')
const ALL = '全部'

function readPack() {
  try { return JSON.parse(fs.readFileSync(store.P.pack, 'utf8')) } catch { return null }
}

// 真人语料：去掉蒸馏时判成鱼的、手动标成鱼/烂/好鱼的。不重新判鱼，直接用 pack 里的结果
function humanItems() {
  const pack = readPack()
  const fishIds = new Set((pack && pack.fishIds) || [])
  const labels = store.labels()
  return store.all()
    .filter((x) => !fishIds.has(x.id) && !['fish', 'bad', 'goodfish'].includes(labels[x.id]))
    .map((x) => ({ ...x, label: labels[x.id] }))
}

// ---- 场合 ----

function sceneNames() {
  return Object.keys((readPack() || {}).scenes || {})
}

// 场合名：先精确，再唯一子串。匹配到好几个就不猜，把候选交回去让调用方报错（新肥鱼 B2：「B」静默选中了第一个场合）
function matchScene(name) {
  const names = sceneNames()
  if (!name) return { hit: null, candidates: [], names }
  if (names.includes(name)) return { hit: name, candidates: [name], names }
  const c = names.filter((k) => k.includes(name))
  return { hit: c.length === 1 ? c[0] : null, candidates: c, names }
}

// 兼容旧调用：只有唯一匹配才算
function resolveScene(name) {
  return matchScene(name).hit
}

// 给 `fishtalk scenes` 用：每个场合多少条、有没有单独的《打法》
function listScenes() {
  const pack = readPack() || {}
  const books = readPlaybooks()
  return Object.entries(pack.scenes || {}).map(([name, s]) => ({ name, count: s.count, playbook: !!books[name] }))
}

function inScene(items, scene) {
  if (!scene) return items
  const { sceneOf } = require('./distill')
  return items.filter((x) => sceneOf(x) === scene)
}

// 当不了范例的：占位符、没内容、太短
function usable(x) {
  const t = x.text
  if (/\[(链接|手机号|身份证号|卡号|地址)\]/.test(t)) return false
  if ([...t].length < 4) return false
  if (new Set(t.replace(/[\s\p{P}\p{S}]/gu, '')).size < 3) return false
  return !/^(前排|沙发|第一|打卡)/.test(t)
}

// ---- like ----

// 常见虚词的两字片段：几乎不算分
const STOP = new Set(('什么 么时 时候 怎么 么样 为什 这个 那个 一个 个人 就是 不是 是不 还是 但是 可以 没有 有没 我们 你们 他们 她们 自己 这么 那么 真的 已经 现在 知道 觉得 感觉 因为 所以 如果 的话 一下 一样 这样 那样 看到 出来 起来 一点 有点 哈哈 啊啊 好像 应该 可能 不能 不会 还有 而且 然后 其实 太长 长了 了吧 了啊 的人 的是 是个 有个 这是 那是 我是 你是 他是 该不 不该').split(' '))

// 纯数字片段（「00」「10」）也算虚的：不然「100万」能匹配到「1:00:05」
const isStop = (k) => STOP.has(k) || /^\d\d$/.test(k)

const oneLine = (t) => t.replace(/\s*\n+\s*/g, ' / ')
const clip = (t, n) => ([...t].length > n ? [...t].slice(0, n).join('') + '…（长评，后面省略）' : t)

function jac(A, B) {
  let n = 0
  for (const x of A) if (B.has(x)) n++
  return n / (A.size + B.size - n || 1)
}

// 加权重叠：分母取「话题」和「评论」里大的那个，比话题还短的评论不额外加分
function overlap(A, B, w) {
  if (!A.size || !B.size) return 0
  let hit = 0, wa = 0, wb = 0
  for (const x of A) { wa += w(x); if (B.has(x)) hit += w(x) }
  for (const x of B) wb += w(x)
  return hit / Math.sqrt(wa * Math.max(wa, wb))
}

// 返回 { list, reason, selfSkipped }。reason：null 正常；'no-topic' 查询里只有虚词；'none' 没找到。
// 新肥鱼 0.3 报告里修的：B1 硬凑（改成相对第一名截断）、B3 原评论自己排第一（跳过）、B7 好结果被说成「参考价值有限」
// （改看第一名盖住了话题的多少）、B13 纯虚词、体验 2 碎片压过完整发言、体验 1 不带来源。
function like(query, opts = {}) {
  const n = opts.n || 10
  const q = grams(query)
  const content = [...q].filter((k) => !isStop(k))
  if (!q.size || !content.length) return { list: [], reason: 'no-topic', selfSkipped: 0 }
  const pool = inScene(humanItems(), opts.scene).filter(usable).map((x) => ({ x, g: grams(x.text) }))
  const df = new Map()
  for (const { g } of pool) for (const k of g) df.set(k, (df.get(k) || 0) + 1)
  const idf = (k) => (isStop(k) ? 0.1 : Math.log((pool.length + 1) / ((df.get(k) || 0) + 1)) + 1)
  // 分母只算语料里出现过的片段：「书什」「货啊」这种跨词片段谁都不会有，算进去只会把覆盖率拉低
  const seen = content.filter((k) => df.has(k))
  if (!seen.length) return { list: [], reason: 'none', selfSkipped: 0 }
  const qWeight = seen.reduce((s, k) => s + idf(k), 0)
  // 话题里大部分片段语料都不认识（「显卡降价了该不该买」只认得「显卡」）：找到的再像也只是沾边
  const known = seen.length / content.length
  const qLen = norm(query).length
  let selfSkipped = 0
  const scored = []
  for (const { x, g } of pool) {
    if (!content.some((k) => g.has(k))) continue // 一个实词片段都没沾上的不要
    // 贴进来的就是语料里的原评论（或它的一大段）：跳过，找的是「别人怎么接」
    const xLen = norm(x.text).length
    // 判定要严：查询的八成都在这条里才算（「DeepSeek 蒸馏闭源」不能因为别人也写了 deepseek 就被跳过）
    if (qLen >= 8 && lcs(query, x.text) >= 0.8 * qLen) { selfSkipped++; continue }
    // 主分看「盖住了话题的多少」，长度只轻轻扣（原来按长度归一，同样沾边时长评论被短碎片压下去：新肥鱼体验 2）
    const hits = content.filter((k) => g.has(k))
    const cover = hits.reduce((a, k) => a + idf(k), 0) / qWeight
    let wb = 0
    for (const k of g) wb += idf(k)
    let s = cover * Math.pow(qWeight / Math.max(qWeight, wb), 0.25) + 0.3 * (x.title ? overlap(q, grams(x.title), idf) : 0)
    if (xLen < 12) s *= Math.sqrt(xLen / 12) // 碎片往后排
    if (content.length >= 3 && hits.length < 2) s *= 0.6 // 只沾上一个片段的多半是巧合
    if (s < 0.06) continue
    // rel 只算相关度，截断和「弱相关」都看它；排序时再加一点赞数
    // 赞数按乘法加：不沾边的高赞句不会被抬上来（0.3.2 复测 N2：「兄弟们实在是太困了」801 赞排到第一）
    scored.push({ x, g, cover, hits: hits.length, rel: s, s: s * (1 + 0.15 * Math.log10(1 + (x.likes || 0))) })
  }
  scored.sort((a, b) => b.s - a.s)
  if (!scored.length) return { list: [], reason: 'none', selfSkipped }
  const top = Math.max(...scored.map((c) => c.rel))
  const picked = []
  for (const c of scored) {
    if (picked.length >= n) break
    if (c.rel < top * (opts.cut ?? 0.4)) continue // 比最对题的差太多的不凑数
    if (picked.some((p) => jac(p.g, c.g) > 0.5)) continue // 别挑一堆差不多的
    picked.push(c)
  }
  // 弱相关看「盖住了话题的多少」，不跟第一名比（复测 N1：第一名是一段长评时，「第二批的我还没发货」这种同题短句全被标弱）。
  // 弱相关的统一排到后面
  const list = picked.map((c) => ({
    text: c.x.text, likes: c.x.likes || 0, where: c.x.title || c.x.where, label: c.x.label,
    score: +c.s.toFixed(3), cover: +c.cover.toFixed(2), weak: c.cover < 0.25 || known < 0.3,
  })).sort((a, b) => a.weak - b.weak)
  return { list, reason: null, selfSkipped }
}

function renderLike(query, res, scene) {
  const r = Array.isArray(res) ? { list: res, reason: res.length ? null : 'none', selfSkipped: 0 } : res
  const L = [`# 跟「${query}」最像的真人发言${scene ? `（${scene}）` : ''}`, '', '> 看人家碰到这种话题怎么接、怎么抖包袱。学路数，别照抄原句；标了来源的梗别搬到别的地方。', '']
  if (r.reason === 'no-topic') L.push('（这句话里没有具体话题，全是「什么时候」「为什么」这种虚词。换一个带实词的说法再找。）')
  else if (r.reason === 'none') L.push('（语料里没找到沾边的。换个说法，或者先喂点这个话题的语料。）')
  else if (r.list.every((e) => e.weak)) L.push('（语料里这个话题的不多，下面只是沾点边的，参考价值有限。）', '')
  for (const e of r.list) {
    const tags = [e.label === 'funny' ? '好笑' : '', e.likes ? `${e.likes} 赞` : '', e.weak ? '弱相关' : ''].filter(Boolean)
    const from = e.where ? `　（来自《${[...e.where].slice(0, 20).join('')}》）` : ''
    L.push(`- ${clip(oneLine(e.text), 200)}${tags.length ? `　〔${tags.join('·')}〕` : ''}${from}`)
  }
  if (r.selfSkipped) L.push('', `（跳过了 ${r.selfSkipped} 条跟你贴的这句几乎一样的：那是原评论本身，不是别人怎么接。）`)
  L.push('')
  return L.join('\n')
}

// ---- 打法 ----

// 新肥鱼 0.3 报告 3.3 节逐条改的：不要百分比（60 条不是随机样本）；看不到「楼上」和回复，就不问对楼上、不问被骂；
// 第 6 条只说相关不说因果；第 8 条每条带例子
const OUTLINE = [
  '接话方式：举 2～3 种这里最常见的接法（顺着视频加码、代入身份、补漏掉的角度、提问……），各配一句原文。不用算百分比。',
  '包袱怎么搭：第一句怎么起，包袱落在哪一句；反转、夸张、自嘲、代入身份……用到哪种就举哪种的例子。',
  '态度：看戏还是站队，真诚还是阴阳，对 UP 主怎么说话。',
  '干货怎么塞：有干货的高赞长什么样，数字、机制、亲身经历是怎么塞进一句话里的，举 2 条。',
  null, // 第 5 条看是不是单场合，见 outline()
  '高赞和没接住的差在哪：材料里「↳」标出来的是同一支视频、同一个话题的对照（组数不多，有的只是「说完整 vs 半句」，按实际情况写）。说说高赞通常多了什么、没接住的通常缺什么。这是相关，不是因果，别打包票。',
  '没接住的说法：哪几类话发了也没人理。（材料只有赞数，看不出谁被骂了，别往那上面写。）',
  '三条打法：照着就能用，一句话一条，每条后面括一个材料里的正例或反例。',
]
function outline(scene) {
  return OUTLINE.map((o) => o || (scene
    ? '梗和黑话：只在这儿好使、搬出去就尴尬的。'
    : '梗和黑话：材料来自好几个地方，说清楚哪些梗只属于哪个来源，混着用会怎样。'))
}

function readPlaybooks() {
  try { return JSON.parse(fs.readFileSync(PLAYBOOKS, 'utf8')) } catch { return {} }
}

// 挑材料。032：大视频把小视频挤没了 → 按来源分组、组内比赞数、各组轮流取。
// 新肥鱼 B4：「中/低」两档几乎全是 0 赞，读不出差别 → 只分两档：「高赞」（组内前 1/4 且有赞，或手动标好笑/好）
// 和「没接住」（组内赞数不过中位数的）。每条高赞先在同一组里找一条同话题的「没接住」配成对照，提纲第 6 条就有得读了。
function sample(items, n) {
  const score = (x) => (x.label === 'funny' ? 1e9 : x.label === 'good' ? 5e8 : 0) + (x.likes || 0)
  const groups = new Map()
  for (const x of items.filter(usable).filter((x) => [...x.text].length <= 200)) {
    const k = x.title || x.where || x.source || '其他'
    if (!groups.has(k)) groups.set(k, [])
    groups.get(k).push({ ...x, _group: k, _g: grams(x.text) })
  }
  const highs = [], lows = []
  for (const list of groups.values()) {
    list.sort((a, b) => score(b) - score(a))
    const q = Math.ceil(list.length / 4)
    const median = list[Math.floor(list.length / 2)].likes || 0
    highs.push(list.filter((x, i) => x.label === 'funny' || x.label === 'good' || (i < q && (x.likes || 0) > median)))
    // 没接住的里优先要有点内容的（8 个字以上），「哈哈」「草」读不出东西
    lows.push(list.filter((x) => !x.label && (x.likes || 0) <= median).sort((a, b) => (norm(b.text).length >= 8) - (norm(a.text).length >= 8)))
  }
  const picked = []
  const taken = new Set()
  const ok = (x) => !taken.has(x) && !picked.some((p) => jac(p._g, x._g) > 0.5)
  const add = (x, extra) => { taken.add(x); const e = { ...x, ...extra }; picked.push(e); return e }
  const roundRobin = (lists, want, fn) => {
    const pos = lists.map(() => 0)
    let got = 0, moved = true
    while (got < want && moved) {
      moved = false
      for (let i = 0; i < lists.length && got < want; i++) {
        while (pos[i] < lists[i].length) {
          const x = lists[i][pos[i]++]
          moved = true
          if (ok(x)) { fn(x, i); got++; break }
        }
      }
    }
    return got
  }
  // 高赞约六成，每条带一个同话题的对照（找得到的话）
  const nHigh = Math.ceil(n * 0.6)
  let budget = n
  roundRobin(highs, nHigh, (h, i) => {
    const e = add(h, { _tier: '高赞' })
    budget--
    if (budget <= 0) return
    let best = null, bs = 0.12
    for (const l of lows[i]) {
      if (!ok(l)) continue
      // 对照也得是句话：至少 8 个字，且不短于高赞那条的四成（「avemujica」对一段长评没法比）
      if (norm(l.text).length < Math.max(8, 0.4 * norm(h.text).length)) continue
      const s = jac(h._g, l._g)
      if (s > bs) { best = l; bs = s }
    }
    if (best) { add(best, { _tier: '没接住', _pairOf: e }); budget-- }
  })
  // 剩下的名额给没配上对的「没接住」，从各组中间段取
  roundRobin(lows.map((l) => l.slice(Math.floor(l.length / 4))), Math.max(0, n - picked.length), (x) => add(x, { _tier: '没接住' }))
  return picked.slice(0, n)
}

function materialLine(x, indent = '') {
  return `${indent}- 〔${x._tier}·${x.likes || 0} 赞${x.label === 'funny' ? '·好笑' : ''}〕${oneLine(x.text)}`
}

// 给 agent 的整份任务：材料 + 提纲 + 怎么存
function brief(opts = {}) {
  const scene = opts.scene || null
  const items = inScene(humanItems(), scene)
  if (items.length < 20) return { ok: false, msg: `${scene ? `「${scene}」` : '语料'}只有 ${items.length} 条真人发言，太少了，读不出打法。先多喂一些（至少 20 条，最好 100 条以上）。` }
  const list = sample(items, opts.n || 60)
  const key = scene || ALL
  const old = readPlaybooks()[key]
  const L = []
  L.push(`# 深度蒸馏：${key}`)
  L.push('')
  L.push(`下面是从 ${items.length} 条真人发言里挑的 ${list.length} 条，按来源分组。「高赞」「没接住」是**在本组里**比的（不同视频热度差很多）。「↳」缩进的那条，是跟上面那条高赞同一个话题、却没什么人赞的对照。`)
  L.push('')
  L.push('请你读完，按提纲写一页《打法》。要求：')
  L.push('- 每一条结论都要能在下面的材料里找到例子，顺手引一句原文当证据（引号括起来，短一点）。找不到例子的别写。')
  L.push('- 写具体的，别写「语言风格轻松幽默」这种对谁都成立的空话。')
  L.push(`- 正文 800 字以内（引的原文不算），用大白话。这页是给以后的你自己在${scene ? '这儿' : '这些地方'}说话前看的。`)
  if (!scene) L.push(`- 材料只来自 ${new Set(list.map((x) => x._group)).size} 个地方，开头写一句这页适用于哪儿，别写成「整个 B 站都这样」。`)
  L.push('')
  L.push('## 提纲')
  L.push('')
  outline(scene).forEach((o, i) => L.push(`${i + 1}. ${o}`))
  L.push('')
  L.push('## 材料')
  L.push('')
  const byGroup = new Map()
  for (const x of list) { if (!byGroup.has(x._group)) byGroup.set(x._group, []); byGroup.get(x._group).push(x) }
  for (const [k, xs] of byGroup) {
    L.push(`### ${[...k].slice(0, 30).join('')}`)
    L.push('')
    for (const x of xs.filter((x) => x._tier === '高赞')) {
      L.push(materialLine(x))
      const pair = xs.find((p) => p._pairOf && p._pairOf.text === x.text)
      if (pair) L.push(materialLine(pair, '  ↳ ').replace('↳ - ', '↳ '))
    }
    for (const x of xs.filter((x) => x._tier === '没接住' && !x._pairOf)) L.push(materialLine(x))
    L.push('')
  }
  L.push('## 写完以后')
  L.push('')
  const sceneArg = scene ? ` --scene "${scene}"` : ''
  L.push(opts.dsh
    ? `再调用一次 \`fishtalk_deep\`，把全文传进 save${scene ? `，scene 填「${scene}」` : ''}。以后 \`fishtalk_pack\` 会把它一起带出来。`
    : `把写好的《打法》存进来：\`fishtalk deep --save 文件路径${sceneArg}\`，或者 \`… | fishtalk deep --save --stdin${sceneArg}\`。以后 \`fishtalk pack\` 会把它一起带出来。`)
  if (old) L.push(`\n（这个场合 ${old.made} 写过一版，当时基于 ${old.basedOn} 条。新写的会覆盖它。）`)
  L.push('')
  return { ok: true, key, count: items.length, text: L.join('\n') }
}

// 覆盖旧的之前把旧的留一份在 playbooks.bak.json（新肥鱼体验 9：忘带 --scene 会静默冲掉「全部」）。
// 返回 overwrote：调用方据此提醒一句
function save(text, opts = {}) {
  const t = String(text || '').trim()
  if (t.length < 30) return { ok: false, msg: '《打法》太短了，没存。' }
  const scene = opts.scene || null
  const key = scene || ALL
  const all = readPlaybooks()
  const overwrote = !!all[key]
  store.ensure()
  if (overwrote) fs.writeFileSync(PLAYBOOKS.replace(/\.json$/, '.bak.json'), JSON.stringify({ [key]: all[key] }, null, 2))
  const made = require('./distill').localTime()
  all[key] = { text: t, made, basedOn: inScene(humanItems(), scene).length }
  fs.writeFileSync(PLAYBOOKS, JSON.stringify(all, null, 2))
  const chars = [...t.replace(/[「“"][^」”"]*[」”"]/g, '')].length
  return { ok: true, key, overwrote, long: chars > 1000 }
}

// pack 时附上的打法：要看的那个场合的，加上总的
function renderPlaybooks(scene) {
  const all = readPlaybooks()
  // 不指定场合时只带总的，别的场合列个名字，免得 pack 越读越长
  const keys = scene ? [scene, ALL] : [ALL]
  const L = []
  const others = Object.keys(all).filter((k) => !keys.includes(k))
  if (others.length) L.push(`> 这些场合还有单独的《打法》：${others.join('、')}。去那儿说话前用 \`fishtalk pack --scene 场合名\`。`, '')
  for (const k of keys) {
    const p = all[k]
    if (!p) continue
    const now = inScene(humanItems(), k === ALL ? null : k).length
    const stale = now >= p.basedOn * 2 && now - p.basedOn >= 100
    L.push(`## 打法 · ${k}`)
    L.push('')
    L.push(`> agent 读语料写的，${p.made}，基于 ${p.basedOn} 条。${stale ? `现在已经有 ${now} 条了，可以重写一版（\`fishtalk deep${k === ALL ? '' : ` --scene "${k}"`}\`）。` : ''}`)
    L.push('')
    L.push(p.text)
    L.push('')
  }
  return L.join('\n')
}

module.exports = { like, renderLike, brief, save, renderPlaybooks, resolveScene, matchScene, listScenes, sceneNames, readPlaybooks, OUTLINE, ALL, PLAYBOOKS }
