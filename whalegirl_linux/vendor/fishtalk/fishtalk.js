#!/usr/bin/env node
// fishtalk（暂名）：只读的语料小工具。让你的 agent 学真人说话，发之前帮它拦一下八股。
// 零依赖，Node 18 以上。
'use strict'

const major = Number(process.versions.node.split('.')[0])
if (major < 18 || typeof fetch !== 'function') {
  console.error(`需要 Node 18 或更高版本（现在是 ${process.versions.node}）。`)
  console.error('给 agent：请帮用户从 https://nodejs.org 装 LTS 版的 Node，装好以后重开命令行，再运行一次。')
  process.exit(2)
}

const fs = require('fs')
const path = require('path')
const store = require('./lib/store')
const { formatScenes, sceneError } = require('./lib/scene-format')

const HELP = `fishtalk：教你的鱼说人话

用法：
  fishtalk collect <BV号或链接>（学视频）    抓这支视频的热门评论和弹幕（只读）
  fishtalk learn bili 知识区（去学）         自动：去 B 站某个分区找热门视频来学（默认 10 个）
  fishtalk learn 贴吧 原神（去学）           自动：去某个吧找热帖来学
  fishtalk feed <文件>（喂）                 喂文本、txt、csv、json、QQ 或微信导出的聊天记录
  fishtalk feed --text "一句话"（喂）        直接喂一句
  fishtalk feed --stdin（喂）                从管道喂（给 agent 用：把读到的评论顺手塞进来）
  fishtalk distill（蒸馏）                   自动蒸馏，生成文风包
  fishtalk label（鱼塘）                     打开鱼塘控制台：分包批注、看文风包、查黑名单、放回误判、粘贴喂料
  fishtalk check "要说的话"（查）            发之前检查：八股、太像鱼、太像自己以前说的、照抄原句
          --keep                           检查通过的话记下来（以后查「别老说一样的」）
          --force                          不跟自己以前说过的话比（同一句想再发一次时用）
  fishtalk pack [--scene 场合]（文风）       打印文风包（agent 说话前读这个）；只看某个场合就加 --scene
  fishtalk pack --like "话题"（文风 --like） 找语料里跟这个话题/这条评论最像的真人发言（默认 10 条，--n 20 改数量）
                                            可能一条都找不到；会跳过你贴的原评论，标「弱相关」和来源
  fishtalk scenes（场合）                    列出场合（名字、条数、有没有单独的《打法》）
  fishtalk deep [--scene 场合]（深度）       深度蒸馏：给 agent 一批材料和提纲，让它读完写一页《打法》
  fishtalk deep --save 文件 [--scene 场合]（深度）
                                            把写好的《打法》存进来（也可以 --stdin），以后 pack 会一起带出来
  （collect、feed、learn 都可以加 --scene 名字，给这批语料标上场合）
  fishtalk status（状态）                    看看现在攒了多少
  fishtalk forget（忘掉）                    清空所有本地数据（要加 --yes 才真删）

  中文名和英文命令等价：fishtalk 鱼塘 / 学视频 BV… / 去学 bili 知识区 / 喂 文件 / 文风 / 查 "…" / 场合 / 深度 / 状态 / 忘掉
  --scene 先精确匹配，再唯一子串；匹配到多个或一个都没有时会报错并列出现有场合，不会悄悄换场合。
  --n 只收正整数。
  数据都在：${store.P.home}（可用环境变量 FISHTALK_HOME 改）`

// 命令行中文名：说中文也行，进 switch 前先映射成英文命令。
// 「打开鱼塘」「打开鱼塘控制台」也收，主人不用记到底是哪几个字。
const COMMAND_ALIASES = {
  鱼塘: 'label',
  打开鱼塘: 'label',
  打开鱼塘控制台: 'label',
  学视频: 'collect',
  去学: 'learn',
  喂: 'feed',
  状态: 'status',
  文风: 'pack',
  查: 'check',
  场合: 'scenes',
  深度: 'deep',
  蒸馏: 'distill',
  忘掉: 'forget',
}

function need(mod, who) {
  try { return require(mod) } catch (e) {
    if (e.code !== 'MODULE_NOT_FOUND') throw e
    console.error(`这个功能（${who}）还没装好：缺 ${mod}`)
    process.exit(3)
  }
}

function readStdin() {
  return new Promise((resolve) => {
    let s = ''
    process.stdin.setEncoding('utf8')
    process.stdin.on('data', (c) => { s += c })
    process.stdin.on('end', () => resolve(s))
  })
}

function autoDistill() {
  // 喂完就自动蒸馏（主人要的「装上就自己学」）
  const pack = require('./lib/distill').run()
  console.log(`已自动蒸馏：真人 ${pack.counts.human} 条，筛掉鱼 ${pack.counts.fish} 条 → ${store.P.packMd}`)
}

async function main() {
  const [rawCmd, ...rest] = process.argv.slice(2)
  const cmd = COMMAND_ALIASES[rawCmd] || rawCmd
  const flag = (f) => rest.includes(f)
  // 值选项：--scene 游戏区 或 --scene=游戏区；--like=、--text=、--n=、--limit=、--save= 都走这里
  const has = (name) => rest.some((x) => x === name || x.startsWith(name + '='))
  const opt = (name) => {
    const eq = rest.find((x) => x.startsWith(name + '='))
    if (eq) return eq.slice(name.length + 1)
    const i = rest.indexOf(name)
    return i >= 0 && rest[i + 1] && !rest[i + 1].startsWith('--') ? rest[i + 1] : null
  }
  const optValues = new Set(['--scene', '--text', '--like', '--n', '--limit', '--save'].map((n) => { const i = rest.indexOf(n); return i >= 0 ? i + 1 : -1 }))
  const positional = rest.filter((x, i) => !x.startsWith('--') && !optValues.has(i))
  const tagScene = (items) => { const s = opt('--scene'); return s ? items.map((x) => ({ ...x, scene: s })) : items }

  // --n 只收正整数；返回 null 表示已经报过错
  const readN = (def) => {
    if (!has('--n')) return def
    const raw = opt('--n')
    if (raw === null || !/^\d+$/.test(raw) || Number(raw) < 1) {
      console.error(`--n 要一个正整数（比如 --n 8），收到「${raw === null ? '' : raw}」。`)
      process.exitCode = 1
      return null
    }
    return Number(raw)
  }

  // 场合：唯一命中才算；多个/零个都报错并列清单，不静默换场合
  const matchOrError = (deep, name, hint) => {
    const m = deep.matchScene(name)
    if (m.hit) return m
    console.error(sceneError(m, name, hint))
    process.exitCode = 1
    return null
  }

  switch (cmd) {
    case 'collect': {
      if (!positional[0]) { console.error('用法：fishtalk collect <BV号或链接>'); process.exitCode = 1; break }
      // BV 号严格是 BV + 10 位，前后不能再接字母数字（测出来：多打一个字母会静默抓成另一支）
      const bv = (positional[0].match(/(?<![0-9A-Za-z])BV[0-9A-Za-z]{10}(?![0-9A-Za-z])/) || [])[0]
      if (!bv) { console.error(`「${positional[0]}」里找不到有效的 BV 号（BV 后面应该正好 10 位字母数字）。`); process.exitCode = 1; break }
      const bili = need('./lib/bili', 'B 站采集')
      let items
      try { items = await bili.collect(bv, {}) } catch (e) {
        const m = e && e.message ? e.message : String(e)
        console.error(/请求错误|-400|-404|不存在|啥都木有/.test(m) ? `找不到 ${bv} 这支视频，可能删了、私密了，或者 BV 号打错了。` : `抓取失败：${m}`)
        process.exitCode = 1
        break
      }
      const n = store.add(tagScene(items))
      console.log(`抓到 ${items.length} 条，新增 ${n} 条。`)
      if (n && !flag('--no-distill')) autoDistill()
      break
    }
    case 'learn': {
      // 自动收集：去某个站点的版块找讨论度高的帖子来学
      const SITES = { bili: 'bili', b站: 'bili', 贴吧: 'tieba', tieba: 'tieba' }
      const site = SITES[(positional[0] || '').toLowerCase()]
      const target = positional.slice(1).join(' ')
      if (!site || !target) { console.error('用法：fishtalk learn bili 知识区 | fishtalk learn 贴吧 原神\n其他论坛：让你的 agent 自己去读帖子，再用 feed --stdin 喂进来。'); process.exitCode = 1; break }
      const mod = need(`./lib/sites/${site}`, `${positional[0]} 自动学习`)
      const limit = Number(opt('--limit')) || 10
      const links = await mod.discover(target, { limit })
      console.log(`找到 ${links.length} 个讨论度高的帖子/视频，开始学……`)
      let got = 0, added = 0
      for (const link of links) {
        try {
          const items = await mod.fetch(link, {})
          got += items.length
          added += store.add(tagScene(items))
        } catch (e) {
          console.log(`  跳过 ${link}：${e.message}`)
          if (/风控|412|-352/.test(e.message)) break
        }
      }
      console.log(`读到 ${got} 条，新增 ${added} 条。`)
      if (added && !flag('--no-distill')) autoDistill()
      break
    }
    case 'feed': {
      const importers = need('./lib/importers', '导入')
      let items
      if (flag('--stdin')) items = importers.parse(await readStdin(), { fromText: true, source: 'text', where: 'stdin' })
      else if (has('--text')) {
        const text = opt('--text')
        if (!text) { console.error('用法：fishtalk feed --text "一句话"'); process.exitCode = 1; break }
        items = importers.parse(text, { fromText: true, source: 'text', where: 'text' })
      }
      else if (positional[0]) {
        // 测出来：路径不存在时会把路径本身当成一句话存进去
        const file = path.resolve(positional[0])
        if (!fs.existsSync(file)) { console.error(`找不到文件：${file}\n（想直接喂一句话，用 --text "……"）`); process.exitCode = 1; break }
        if (fs.statSync(file).isDirectory()) { console.error(`这是个文件夹：${file}\n请指定里面的某个文件（txt、csv、json、聊天记录导出）。`); process.exitCode = 1; break }
        items = importers.parse(file, {})
      }
      else { console.error('用法：fishtalk feed <文件> | --text "一句话" | --stdin'); process.exitCode = 1; break }
      const n = store.add(tagScene(items))
      console.log(`读到 ${items.length} 条，新增 ${n} 条。`)
      if (n && !flag('--no-distill')) autoDistill()
      break
    }
    case 'distill': {
      const items = store.all()
      if (!items.length) {
        console.log('语料 0 条，没有生成文风包（原来的文风包保留）。')
        break
      }
      const pack = require('./lib/distill').run()
      console.log(`蒸馏完成：语料 ${pack.counts.total} 条，真人 ${pack.counts.human}，鱼 ${pack.counts.fish}，批注 ${pack.counts.labeled}。`)
      console.log(`文风包：${store.P.packMd}`)
      break
    }
    case 'label':
      require('./lib/label/server').start({ noOpen: flag('--no-open') })
      break
    case 'scenes': {
      const deep = require('./lib/deep')
      console.log(formatScenes(deep.listScenes()))
      break
    }
    case 'check': {
      // 只去掉 check 自己认识的开关；其他以 - 开头的（比如「-- 我不同意」）都算句子内容
      const CHECK_FLAGS = new Set(['--keep', '--json', '--allow-copy', '--force'])
      const text = rest.filter((x) => !CHECK_FLAGS.has(x)).join(' ')
      if (!text.trim()) { console.error('用法：fishtalk check "要说的话" [--keep] [--json] [--allow-copy] [--force]'); process.exitCode = 1; break }
      const r = require('./lib/check').run(text, { keep: flag('--keep'), allowCopy: flag('--allow-copy'), ignoreHistory: flag('--force') })
      if (flag('--json')) console.log(JSON.stringify(r))
      else {
        console.log(r.ok ? '✓ 可以发' : '✗ 先别发')
        for (const p of r.problems) console.log('  - ' + p)
        for (const n of r.notes) console.log('  · ' + n)
      }
      process.exitCode = r.ok ? 0 : 1
      break
    }
    case 'pack': {
      const deep = require('./lib/deep')
      const sceneRaw = has('--scene') ? opt('--scene') : null
      if (has('--scene') && !sceneRaw) { console.error('用法：fishtalk pack [--scene 场合] | fishtalk pack --like "话题" [--scene 场合] [--n 10]'); process.exitCode = 1; break }
      const likeRaw = has('--like') ? opt('--like') : null
      if (has('--like') && !likeRaw) { console.error('用法：fishtalk pack --like "话题或要回的那条评论" [--scene 场合] [--n 10]'); process.exitCode = 1; break }
      const n = has('--like') ? readN(10) : 10
      if (n === null) break
      if (!fs.existsSync(store.P.packMd)) { console.error('还没有文风包。先喂点语料，再运行 fishtalk distill。'); process.exitCode = 1; break }
      let hit = null
      if (sceneRaw) {
        const m = matchOrError(deep, sceneRaw, '（不填 --scene 才是全部语料。）')
        if (!m) break
        hit = m.hit
      }
      if (has('--like')) {
        process.stdout.write(deep.renderLike(likeRaw, deep.like(likeRaw, { scene: hit, n }), hit))
        break
      }
      if (hit) {
        const p = JSON.parse(fs.readFileSync(store.P.pack, 'utf8'))
        const md = require('./lib/distill').renderScene(p, hit)
        if (!md) { console.error(`「${hit}」没有单独成节（够 30 条才会单独成一节）。`); process.exitCode = 1; break }
        process.stdout.write(md + deep.renderPlaybooks(hit))
        break
      }
      process.stdout.write(fs.readFileSync(store.P.packMd, 'utf8') + deep.renderPlaybooks(null))
      break
    }
    case 'deep': {
      const deep = require('./lib/deep')
      const sceneRaw = has('--scene') ? opt('--scene') : null
      if (has('--scene') && !sceneRaw) { console.error('用法：fishtalk deep [--scene 场合] [--n 60] | fishtalk deep --save 文件 [--scene 场合]'); process.exitCode = 1; break }
      let hit = null
      if (sceneRaw) {
        const m = matchOrError(deep, sceneRaw, '（不填 --scene 就是对全部语料做。）')
        if (!m) break
        hit = m.hit
      }
      if (has('--save')) {
        let text = null
        if (flag('--stdin')) text = await readStdin()
        else {
          const saveArg = opt('--save') || positional[0]
          if (!saveArg) { console.error('用法：fishtalk deep --save <写好的打法.md> [--scene 场合]，或者 … | fishtalk deep --save --stdin'); process.exitCode = 1; break }
          const file = path.resolve(saveArg)
          if (!fs.existsSync(file)) { console.error(`找不到文件：${file}\n（想从管道喂，用 … | fishtalk deep --save --stdin）`); process.exitCode = 1; break }
          if (fs.statSync(file).isDirectory()) { console.error(`这是个文件夹：${file}\n请指定里面的某个 .md 文件。`); process.exitCode = 1; break }
          text = fs.readFileSync(file, 'utf8')
        }
        const r = deep.save(text, { scene: hit })
        if (!r.ok) { console.error(r.msg); process.exitCode = 1; break }
        console.log(`存好了：《打法 · ${r.key}》。以后 fishtalk pack${hit ? ` --scene "${hit}"` : ''} 会一起带出来。`)
        if (r.overwrote) console.log(`覆盖了旧的《打法 · ${r.key}》，旧版在 ${path.join(store.P.home, 'playbooks.bak.json')}。`)
        if (r.long) console.log('超过 1000 字了（引的原文不算）；能压就压一点，pack 会短一些。')
        break
      }
      const n = readN(60)
      if (n === null) break
      const r = deep.brief({ scene: hit, n })
      if (!r.ok) { console.error(r.msg); process.exitCode = 1; break }
      process.stdout.write(r.text)
      break
    }
    case 'status': {
      const items = store.all()
      const by = {}
      for (const x of items) by[x.source] = (by[x.source] || 0) + 1
      console.log(`语料 ${items.length} 条：${Object.entries(by).map(([k, v]) => `${k} ${v}`).join('，') || '还没有'}`)
      console.log(`手动批注 ${Object.keys(store.labels()).length} 条；发过的话 ${store.said().length} 条`)
      console.log(fs.existsSync(store.P.packMd) ? `文风包：${store.P.packMd}` : '还没有文风包')
      break
    }
    case 'forget':
      if (!flag('--yes')) return console.log(`会删掉 ${store.P.home} 里的所有数据。确定的话运行：fishtalk 忘掉 --yes（或 fishtalk forget --yes）`)
      store.forget()
      console.log('清空了。')
      break
    default:
      if (cmd) { console.error(`不认识的命令：${cmd}`); process.exitCode = 1 }
      console.log(HELP)
  }
}

main().catch((e) => {
  console.error('出错了：' + (e && e.message ? e.message : e))
  // 不用 process.exit：网络连接还没关干净时强退，Windows 上会触发 Node 的断言崩溃（测出来的）
  process.exitCode = 1
})
