// DSH 插件入口（照大肥鱼 029 的方案）：在 DSH 里注册五个工具和一份 skill。
// 逻辑都在 lib/ 里（CommonJS），这里只做适配。
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')

export const name = 'fishtalk'
export const inject = ['tools']

const asText = (value) => [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }]

function obj(properties, required = []) {
  return { type: 'object', properties, required, additionalProperties: false }
}

// DSH 参数是 number，但模型可能传 0/负数/小数；只收正整数
function positiveInt(value, fallback) {
  if (value === undefined || value === null) return fallback
  return Number.isInteger(value) && value > 0 ? value : null
}

export function apply(ctx) {
  const store = require('../store')
  const distill = () => require('../distill').run()
  const { formatScenes, sceneError } = require('../scene-format')

  const tools = [
    {
      name: 'fishtalk_pack',
      description: '读文风包：说话之前先读这个，照里面的语气、开头方式、说法说话（学味道，别照抄范例）。传 like 就改成找语料里跟这个话题/这条评论最像的真人发言；可能一条都找不到，会跳过原评论、标「弱相关」和来源，不硬凑。传 scenes 只列场合（名字、条数、有没有《打法》）。',
      parameters: obj({
        scene: { type: 'string', description: '只看某个场合（比如要去评论的那支视频的标题关键词、「QQ 聊天」）；不填就看全部。先精确匹配，再唯一子串；匹配到多个会报错并列候选' },
        like: { type: 'string', description: '要聊的话题，或者你要回的那条评论原文' },
        n: { type: 'number', description: 'like 时返回几条，默认 10；只收正整数' },
        scenes: { type: 'boolean', description: '只列出场合名、条数、有没有单独的《打法》，不读文风包' },
      }),
      async execute(args = {}) {
        const deep = require('../deep')
        if (args.scenes) return formatScenes(deep.listScenes())
        let hit = null
        if (args.scene) {
          const m = deep.matchScene(String(args.scene))
          if (!m.hit) return sceneError(m, String(args.scene), '（不填 scene 才是全部语料。）')
          hit = m.hit
        }
        let n = 10
        if (args.like) {
          n = positiveInt(args.n, 10)
          if (n === null) return 'n 要一个正整数（比如 10）。'
        }
        if (!fs.existsSync(store.P.packMd)) return '还没有文风包。先用 fishtalk_feed 喂一些真人发言，或者用 fishtalk_learn 学一个版块。'
        if (args.like) return deep.renderLike(String(args.like), deep.like(String(args.like), { scene: hit, n }), hit)
        if (hit) {
          const md = require('../distill').renderScene(JSON.parse(fs.readFileSync(store.P.pack, 'utf8')), hit)
          if (!md) return `「${hit}」没有单独成节（够 30 条才会单独成一节）。`
          return md + deep.renderPlaybooks(hit)
        }
        return fs.readFileSync(store.P.packMd, 'utf8') + deep.renderPlaybooks(null)
      },
    },
    {
      name: 'fishtalk_deep',
      description: '深度蒸馏（要烧你自己的模型，先问主人）。不传 save：拿到一批有代表性的真人发言和一份固定提纲，读完按提纲写一页《打法》（正文 800 字以内，引的原文不算）。写好后再调一次，把全文传进 save 存起来；覆盖旧版会先备份到 playbooks.bak.json，超过 1000 字会提醒。',
      parameters: obj({
        scene: { type: 'string', description: '只做某个场合；不填就是全部语料。先精确匹配，再唯一子串；匹配到多个会报错并列候选' },
        save: { type: 'string', description: '写好的《打法》全文' },
        n: { type: 'number', description: '材料条数，默认 60；只收正整数' },
      }),
      async execute(args = {}) {
        const deep = require('../deep')
        let hit = null
        if (args.scene) {
          const m = deep.matchScene(String(args.scene))
          if (!m.hit) return sceneError(m, String(args.scene), '（不填 scene 就是对全部语料做。）')
          hit = m.hit
        }
        if (args.save) {
          const r = deep.save(args.save, { scene: hit })
          if (!r.ok) return r.msg
          let msg = `存好了：《打法 · ${r.key}》。`
          if (r.overwrote) msg += `\n覆盖了旧的《打法 · ${r.key}》，旧版在 ${path.join(store.P.home, 'playbooks.bak.json')}。`
          if (r.long) msg += '\n超过 1000 字了（引的原文不算）；能压就压一点，pack 会短一些。'
          return msg
        }
        const n = positiveInt(args.n, 60)
        if (n === null) return 'n 要一个正整数（比如 60）。'
        const r = deep.brief({ scene: hit, n, dsh: true })
        return r.ok ? r.text : r.msg
      },
    },
    {
      name: 'fishtalk_check',
      description: '发出去之前检查一句话：鱼八股原句、AI 腔、跟别的鱼太像、跟自己以前说的太像、照抄语料原句。只报问题，不改写。通过了且真的发出去了，keep 传 true 记下来。',
      parameters: obj({ text: { type: 'string', description: '要发的那句话' }, keep: { type: 'boolean', description: '通过后记为「已说过」' } }, ['text']),
      async execute(args) {
        return require('../check').run(args.text, { keep: !!args.keep })
      },
    },
    {
      name: 'fishtalk_feed',
      description: '把你已经读到的真人发言（评论、弹幕、帖子、聊天）喂进来学，学完自动蒸馏。只喂本来就读到的，不要为了它专门去多抓。',
      parameters: obj({
        lines: { type: 'array', items: { type: 'string' }, description: '一条发言一项' },
        where: { type: 'string', description: '从哪读到的，比如 BV 号、帖子标题、群名' },
        source: { type: 'string', description: 'bili / tieba / qq / wechat / text …' },
        scene: { type: 'string', description: '场合名，同一类地方用同一个名字（比如「原神吧」「技术群」），口气会分开蒸馏' },
      }, ['lines']),
      async execute(args) {
        const day = new Date().toISOString().slice(0, 10)
        const items = (args.lines || []).map((text) => ({ text, source: args.source || 'text', where: args.where || 'agent', kind: 'line', day, ...(args.scene ? { scene: args.scene } : {}) }))
        const added = store.add(items)
        const pack = added ? distill() : null
        // 没有新增就不蒸馏；这里必须是 0，不能是 undefined——undefined 会让 DSH 判成非法输出
        return { read: items.length, added, human: pack ? pack.counts.human : 0, fish: pack ? pack.counts.fish : 0 }
      },
    },
    {
      name: 'fishtalk_learn',
      description: '自动学：去 B 站某个分区（或「搜:关键词」）找讨论度高的视频，抓高赞评论和弹幕来学，学完自动蒸馏。比较慢（每个请求间隔几秒），默认学 5 个。贴吧不登录会被安全验证挡住，遇到就改用你自己的浏览器读帖子再 fishtalk_feed。',
      parameters: obj({
        site: { type: 'string', enum: ['bili', 'tieba'] },
        target: { type: 'string', description: '分区名，如「知识区」；或「搜:关键词」；贴吧填吧名' },
        limit: { type: 'number', description: '学几个视频或帖子，默认 5' },
      }, ['site', 'target']),
      async execute(args, exec) {
        const mod = require(`../sites/${args.site}`)
        const links = await mod.discover(args.target, { limit: args.limit || 5 })
        let got = 0, added = 0
        const skipped = []
        for (const link of links) {
          if (exec && exec.signal && exec.signal.aborted) break
          try {
            const items = await mod.fetch(link, {})
            got += items.length
            added += store.add(items)
          } catch (e) {
            skipped.push(`${link}：${e.message}`)
            if (/风控|blocked|412|-352/.test(e.message)) break
          }
        }
        const pack = added ? distill() : null
        return { found: links.length, read: got, added, human: pack ? pack.counts.human : 0, skipped }
      },
    },
  ]

  for (const t of tools) {
    const { execute, ...def } = t
    ctx.effect(() => ctx.tools.register({
      ...def,
      output: { schema: {}, render: (_a, value) => asText(value) },
      execute,
    }), `fishtalk: ${t.name}`)
  }

  // skill 可选：有 skills 服务才注册（029 第 3 节第 2 条）
  if (typeof ctx.inject === 'function') {
    ctx.inject(['skills'], (c) => {
      const content = fs.readFileSync(path.join(ROOT, 'skills', 'fishtalk', 'SKILL.md'), 'utf8').replace(/^---[\s\S]*?---\s*/, '')
      const dispose = c.skills.register({
        name: 'fishtalk',
        description: '说话像真人、别写鱼八股：说话前读文风包（可带场合、可找类似发言），发之前 check，读到真人发言顺手喂进来。',
        source: 'bundled',
        content,
      })
      c.effect(() => dispose, 'fishtalk: skill')
    })
  }
}
