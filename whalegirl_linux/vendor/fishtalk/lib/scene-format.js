// 场合的显示与报错：CLI 和 DSH 工具共用一套话，免得两边说法不一样。
'use strict'

function formatScenes(list) {
  if (!list || !list.length) return '还没有单独成节的场合（够 30 条才会单独成一节）。'
  const L = [`场合（${list.length} 个）：`]
  for (const s of list) L.push(`  - ${s.name}（${s.count} 条）${s.playbook ? '〔有打法〕' : ''}`)
  return L.join('\n')
}

// m 是 deep.matchScene() 的返回值；hint 是可选的下一步提示
function sceneError(m, name, hint) {
  const lines = []
  if (m.candidates.length > 1) {
    lines.push(`「${name}」匹配到多个场合，请写完整：`)
    for (const c of m.candidates) lines.push(`  - ${c}`)
  } else if (m.names.length) {
    lines.push(`没有「${name}」这个场合。现有场合：`)
    for (const c of m.names) lines.push(`  - ${c}`)
  } else {
    lines.push(`没有「${name}」这个场合。`)
    lines.push('还没有单独成节的场合（够 30 条才会单独成一节）。')
  }
  if (hint) lines.push(hint)
  return lines.join('\n')
}

module.exports = { formatScenes, sceneError }
