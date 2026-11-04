// 判断一条语料是不是「鱼」（AI 八股）。有大肥鱼写的 fishrules.js 就用它，没有就用内置的保底规则。
'use strict'

const { hits } = require('./blacklist')
const { grams, jaccard } = require('./sim')

let rules = null
try { rules = require('./fishrules') } catch { /* 还没写好就用保底 */ }

// 保底规则：撞黑名单，或者跟别的来源里的话高度雷同（同一套提示词刷出来的）
function fallback(item, ctx) {
  const reasons = hits(item.text)
  if (ctx && ctx.others) {
    const g = grams(item.text)
    for (const o of ctx.others) {
      if (o.where === item.where) continue
      if (item.text.length >= 8 && jaccard(g, o._g || (o._g = grams(o.text))) >= 0.75) {
        reasons.push('跟别处的评论几乎一样（疑似同一套提示词）')
        break
      }
    }
  }
  return { fish: reasons.length > 0, reasons }
}

function score(item, ctx) {
  if (rules && typeof rules.score === 'function') {
    try { return rules.score(item, ctx) } catch { /* 规则出错时退回保底 */ }
  }
  return fallback(item, ctx)
}

module.exports = { score }
