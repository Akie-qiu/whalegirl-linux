"""Render the synthetic replay evidence into a self-contained local report."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/focus-evaluation-20261105'
RUNS = OUT / 'results/runs'
CASES = ('topic', 'tired', 'others', 'resolved', 'banter', 'preference', 'topic_owner')
ARM_NAMES = {'baseline': '当前线上代码基线', 'candidate': '本地候选版本'}
NOTES = {
    'topic': '候选首句接住蛋糕塌了的自嘲；基线多出“第一次做”的假设和建议。两者都没有转回电脑话题。',
    'tired': '候选顺着会议超时吐槽；基线先复述，再突然插入周四梗。',
    'others': '两版都插入了别人之间的安排，未选择旁观。候选更短，仍未达到参与判断的预期。',
    'resolved': '候选第一句收尾较自然，但两版在点头表情后都继续补话，没有自然结束。',
    'banter': '候选沿着早睡的语境调侃；基线编出了“上次的一把打到天亮”，已有材料没有这件具体往事。',
    'preference': '候选允许今天全糖、明天再说；基线附加健康提醒，并提前把明天安排回无糖。',
    'topic_owner': '同一人的无关旧偏好：基线两轮各自动加载2条，候选两轮均为0。两版都没有真正扯回电脑，但候选仍多问一句要看蛋糕。',
}


def read(path):
    return json.loads(path.read_text('utf-8'))


def primary_path(case, arm):
    probe = RUNS / f'probe-{case}-{arm}/result.json'
    return probe if probe.exists() else RUNS / f'{case}-{arm}/result.json'


def quantiles(values):
    values = sorted(values)
    if not values:
        return {'n': 0, 'p50': None, 'p95': None, 'max': None}
    return {'n': len(values), 'p50': round(statistics.median(values), 4),
            'p95': round(values[math.ceil(len(values) * .95) - 1], 4), 'max': values[-1]}


def aggregate(rows):
    turns = [turn for row in rows for turn in row.get('turns', [])]
    usage = {}
    for key in ('requests', 'successful', 'failed', 'input_tokens', 'output_tokens',
                'total_tokens', 'cached_tokens', 'uncached_tokens', 'reasoning_tokens'):
        usage[key] = sum((row.get('usage', {}).get('summary', {}).get(key) or 0) for row in rows)
    usage['cache_hit_pct'] = round(100 * usage['cached_tokens'] / usage['input_tokens'], 2)
    timing = {}
    for name, subset in (
        ('all', turns),
        ('text_only', [t for t in turns if t['sent'] and all(a['type'] == 'text' for a in t['sent'])]),
        ('with_sticker', [t for t in turns if any(a['type'] == 'sticker' for a in t['sent'])]),
    ):
        timing[name] = quantiles([t['elapsed_seconds'] for t in subset])
    return {'runs': len(rows), 'turns': len(turns), 'usage': usage, 'timing': timing,
            'states': dict(Counter(t.get('state') for t in turns)),
            'peak_rss_mib': max(row['peak_rss_mib'] for row in rows),
            'fishtalk_reference_turns': sum(
                (t.get('metrics', {}).get('fishtalk_reference') or {}).get('sample_count', 0) > 0 for t in turns),
            'additional_requests': sum(max(0, len(t.get('calls', [])) - 1) for t in turns)}


def table(headers, rows):
    return '<div class="table"><table><thead><tr>' + ''.join(f'<th>{escape(str(x))}</th>' for x in headers) + \
        '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{escape(str(x))}</td>' for x in row) +
                                        '</tr>' for row in rows) + '</tbody></table></div>'


def md_table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |',
                      *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]])


def conversation(row, label, arm):
    parts = [f'<article class="chat"><h3>{escape(label)} <span class="identity">{ARM_NAMES[arm]}</span></h3>']
    for turn in row['turns']:
        parts.append('<div class="human"><small>' + escape(turn['sender']) + '</small><div>' +
                     escape(turn['input']) + '</div></div>')
        if not turn['sent']:
            parts.append('<div class="silent">未发送 · ' + escape(str(turn.get('state'))) + '</div>')
        for action in turn['sent']:
            text = action['text'] if action['type'] == 'text' else '［表情：' + action['text'] + '］'
            parts.append('<div class="bot ' + ('sticker' if action['type'] == 'sticker' else '') + '">' + escape(text) + '</div>')
        parts.append(f'<small class="timing">处理 {turn["elapsed_seconds"]:.2f} 秒 · 模型 {len(turn["calls"])} 次 · {escape(str(turn.get("state")))}</small>')
    parts.append('</article>')
    return ''.join(parts)


def transcript_md(row, label):
    lines = [f'**{label}**', '']
    for turn in row['turns']:
        lines.extend([f'{turn["sender"]}：{turn["input"]}', ''])
        if not turn['sent']:
            lines.extend([f'机器人：未发送（{turn.get("state")}）', ''])
        for action in turn['sent']:
            text = action['text'] if action['type'] == 'text' else '［表情：' + action['text'] + '］'
            lines.extend(['机器人：' + text.replace('\n', ' / '), ''])
        lines.extend([f'处理 {turn["elapsed_seconds"]:.2f} 秒；实际模型请求 {len(turn["calls"])} 次。', ''])
    return '\n'.join(lines)


def main():
    progress = read(OUT / 'results/progress.json')
    final = read(OUT / 'final-verification.json')
    pairs = read(OUT / 'anonymous.json')
    mapping = read(OUT / 'mapping.json')
    primary = {arm: [read(primary_path(case, arm)) for case in CASES] for arm in ARM_NAMES}
    all_results = {arm: [read(path) for path in sorted(RUNS.glob(f'*-{arm}/result.json'))
                         if not path.parent.name.startswith('pressure-')] for arm in ARM_NAMES}
    stats = {arm: aggregate(rows) for arm, rows in all_results.items()}
    primary_stats = {arm: aggregate(rows) for arm, rows in primary.items()}
    pressure = {label: read(RUNS / f'pressure-{label}/result.json') for label in
                ('baseline100', 'candidate100', 'candidate40')}
    host_samples = [sample for run in progress['runs'] for sample in run['samples']]
    host = {'min_available_mib': min(s['available_mib'] for s in host_samples),
            'max_swap_used_mib': max(s['swap_used_mib'] for s in host_samples),
            'max_load1': max(s['load_1m'] for s in host_samples),
            'max_memory_psi_avg10': max(float(s['memory_psi']['avg10']) for s in host_samples),
            'health_all_ok': all(r['health_after'].get('gateway') == 'logged_in' and not r['health_after'].get('paused')
                                 for r in progress['runs'])}
    snapshot = {'all_runs': stats, 'primary_seven_pairs': primary_stats, 'host': host,
                'final': final, 'pressure': {k: {field: d[field] for field in ('phases', 'peak_rss_mib', 'cpu_seconds')}
                                            for k, d in pressure.items()}}
    (OUT / 'metrics-summary.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), 'utf-8')
    model = primary['candidate'][0]['config']
    when = datetime.fromtimestamp(progress['finished'], timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    summary = '当前话题与记忆筛选有改善；活人感仍未达到预期。受限场景下的资源测试通过，不能据此保证长期线上容量。'
    method = [
        f'时间：{when}（北京时间）。基线取实际线上发布源码，候选在同一源码快照上合入本地改动；没有覆盖服务器上未提交的源码。',
        f'同一远程模型 {model["model"]}、相同角色卡哈希、实际温度 {model["temperature"]}、思考等级 {model["reasoning"]}，最近100条。当前保存温度为空，程序实际回退到0.55；没有把后台空值当成1.2。',
        '7组两轮对话×新旧两版=28轮；另保留别人互聊、表情收尾的重复试验8轮，总计36轮、39次真实API请求（基线20、候选19）。两次脚本初始化失败未调用模型，不冒充机器人失败。',
        '只用合成人物ID、合成历史与同群合成文风语料；每段第一轮实际输出会进入第二轮上下文。未把真实私人聊天上传给模型。',
        '对话经真实Runtime处理，但收取、发送网关替换为隔离适配器；关闭了搜索、生图等旁支及真实发送等待。耗时不含线上轮询、微信界面操作和实际表情传输。',
        '观察时先保存匿名片段判断，再揭示版本；由同一个助手做定性评阅，不是独立真人盲评。配置核查时已看过topic及topic_owner身份，不用它们宣称严格盲评胜率。',
        '核实生产配置：自然接话已开启，自主引擎未开启。早期对唤醒入口的疑点未成立；重复试验与原始片段均保留。运行任务直接给予一次处理机会，不代表已验证线上准入、静默、调度与收取全过程。',
        '核心角色卡没有改写；没有部署、推送、改生产配置或删数据。所有模型请求都使用内存中的凭据，报告不含密钥。',
    ]
    findings = [
        '有进步：蛋糕、会议吐槽、最后一把、奶茶选择等片段较贴合当前话头，少了部分建议和编造的具体往事。样本少且采样随机，不能外推为稳定效果。',
        '记忆机制：同一人的旧电脑偏好测试中，无关自动加载由每轮2条降为0条。两版实际回复都未跑回电脑话题，不能把召回条数变化说成已量化消除了跑题。',
        '仍不够自然：候选在别人安排下楼时插话；在点头表情后继续补“下次先怀疑引号”，不会自然收尾。7组主回放的两版各14轮全部发送，没有展示沉默判断。',
        '文风利用不足：候选主回放14轮仅1轮加载到一条情境样例；统计性文风概况仍在，但多数话题无适配例句。当前合成语料不能验证真人长期学习或互联网表达共享已显著改变行为。',
        '协议仍有代价：36轮中3轮走了已有JSON格式修复，各多一次模型调用；基线2轮、候选1轮。HTTP请求最终成功，但不能声称次次一次调用或格式零问题。',
        '基础压力可接受：4群48条突发合并为4次模拟模型调用，44条被合并、4条判断后忽略、队列清空；共享锁内同时执行数为1。这是替身模型与隔离数据库结果，未测试真实API并发或微信发送吞吐。',
        '完整长正文通过：100条中当前短消息1条、长正文99条，候选完整保留99段（112871字符）；基线99段被截短。该极端输入仅测构造，没有送远程模型，仍受模型上下文窗口约束。',
    ]
    requests = sum(s['usage']['requests'] for s in stats.values())
    tokens = sum(s['usage']['total_tokens'] for s in stats.values())
    resources = [
        ('100条预处理 p50', f'{pressure["baseline100"]["phases"][0]["p50_seconds"]*1000:.2f} ms', f'{pressure["candidate100"]["phases"][0]["p50_seconds"]*1000:.2f} ms'),
        ('100条预处理 p95', f'{pressure["baseline100"]["phases"][0]["p95_seconds"]*1000:.2f} ms', f'{pressure["candidate100"]["phases"][0]["p95_seconds"]*1000:.2f} ms'),
        ('压力进程峰值RSS', f'{pressure["baseline100"]["peak_rss_mib"]:.2f} MiB', f'{pressure["candidate100"]["peak_rss_mib"]:.2f} MiB'),
        ('60轮预处理+突发+长文CPU时间', f'{pressure["baseline100"]["cpu_seconds"]:.2f} s', f'{pressure["candidate100"]["cpu_seconds"]:.2f} s'),
        ('高字数100条完整长正文', '0 / 99 段', '99 / 99 段'),
        ('4群48条突发最大锁内并发', '1', '1'),
    ]
    token_rows = []
    for label, key in [('实际请求', 'requests'), ('输入Token', 'input_tokens'), ('输出Token', 'output_tokens'),
                       ('总Token', 'total_tokens'), ('缓存输入Token', 'cached_tokens'), ('未缓存输入Token', 'uncached_tokens')]:
        token_rows.append((label, f'{stats["baseline"]["usage"][key]:,}', f'{stats["candidate"]["usage"][key]:,}'))
    token_rows += [('缓存命中率', f'{stats["baseline"]["usage"]["cache_hit_pct"]:.2f}%', f'{stats["candidate"]["usage"]["cache_hit_pct"]:.2f}%'),
                   ('失败的HTTP/API请求', '0', '0'), ('额外格式修复请求', '2', '1')]
    latency_rows = []
    for kind, name in [('text_only', '只发文字'), ('with_sticker', '含表情动作（发送为替身）')]:
        a, b = primary_stats['baseline']['timing'][kind], primary_stats['candidate']['timing'][kind]
        latency_rows.append((name, f'{a["n"]}轮 / {a["p50"]:.2f}s / {a["p95"]:.2f}s',
                             f'{b["n"]}轮 / {b["p50"]:.2f}s / {b["p95"]:.2f}s'))
    limits = [
        '测试作业串行，在同一2核约1.73GiB服务器以192MiB内存上限、禁用作业swap、50%单核CPU配额运行；生产服务未停。',
        f'测试期间主机最低可用内存 {host["min_available_mib"]:.2f} MiB，swap始终 {host["max_swap_used_mib"]:.2f} MiB；最高1分钟负载 {host["max_load1"]:.2f}。未触发测试内存保护，21次阶段健康检查均在线、无发送暂停。',
        f'结束核对：可用内存 {final["available_mib"]:.2f} MiB，磁盘余量 {final["disk_free_gib"]:.3f} GiB，微信在线；源码仍为原发布，受保护配置在测试前后哈希相同。',
        '测试隔离目录合计约43.3MiB，包括两次脚本初始化尝试，未下载视频或复制生产数据库。没有自动删除，以遵守删除前报告的约定。',
        '受限短测足以发现本轮预处理没有明显拖垮主机；不能代替长时间稳定性、真实微信收发、视频处理和成长任务同时运行的压力验收。服务器空闲内存仍少，不据此提高生产并发。',
        'p95采用最近秩法，小样本时接近或等于最大值。缓存来自接口回执；不同请求顺序会影响缓存，62.44%与62.68%的微小差别不能证明稳态命中提升。',
        'Token输入、输出、总量均有接口精确回执；推理Token为输出组成项，不再加进总量。隔离测试未导入价格配置，费用未报告，不能用空费用当作零元。',
    ]
    next_steps = [
        '先解决自然收尾和接话对象：减少彼此冲突的回复要求，由同一次模型判断决定是否加入；避免再堆固定短句或新增每轮审稿调用。',
        '针对文风参考未命中的问题，用经授权的真人对话重新做“学过语料/未见话题”的留出对比；不能只看是否有学习记录。',
        '单独排查原有JSON协议不稳定，保留有效旁观结果；避免格式恢复给一次本该沉默的机会补出回复。',
        '当前建议：保留本地候选与测试证据，活人感继续调整后再做小规模回放；本报告不作为上线通过结论。',
    ]
    css = '''body{margin:0;background:#f4f5f8;color:#202a38;font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1180px;margin:auto;padding:30px 22px 70px}h1{font-size:30px;line-height:1.3}h2{margin-top:42px;font-size:23px}h3{font-size:17px}p{max-width:1000px}.banner{background:#fff1ce;border-left:5px solid #deab36;padding:16px 20px;border-radius:10px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.card,.scene{background:white;border:1px solid #dde1e8;border-radius:14px;padding:20px}.card strong{display:block;font-size:25px}.card small,.timing,small{color:#647184}.table{overflow:auto;background:white;border-radius:10px;border:1px solid #dde1e8}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:10px 14px;border-bottom:1px solid #e6e9ee}th{background:#eaf0f7}.scene{margin:20px 0}.duo{display:grid;grid-template-columns:1fr 1fr;gap:22px}.chat{background:#f6f8fb;border-radius:12px;padding:16px}.human{margin:18px 0 12px;max-width:90%}.human div{background:#e5eaf1;border-radius:10px;padding:9px 12px;white-space:pre-wrap}.bot{background:#daeee9;margin:7px 0 7px auto;border-radius:10px;padding:8px 12px;max-width:85%;width:fit-content;white-space:pre-wrap}.sticker{background:#fff0ce;font-size:14px}.timing{display:block;text-align:right;font-size:12px;margin-bottom:22px}.silent{color:#8e6600;text-align:right}.identity{display:none;color:#365c87;font-size:13px}.revealed .identity{display:inline}details{margin:20px 0;background:#fff;padding:16px;border-radius:10px}summary{cursor:pointer;font-weight:600}li{margin:9px 0}.note{color:#745100;background:#fff5df;padding:10px 13px;border-radius:8px}.nav a{display:inline-block;margin:4px 10px 4px 0;color:#2b587d}button{font:inherit;border:0;border-radius:7px;background:#284f70;color:white;padding:9px 17px;cursor:pointer}footer{color:#667384;font-size:13px;margin-top:35px}@media(max-width:720px){.duo,.cards{grid-template-columns:1fr}main{padding:18px 12px}h1{font-size:25px}}'''
    def bullets(items):
        return '<ul>' + ''.join('<li>' + escape(x) + '</li>' for x in items) + '</ul>'
    html = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>DeepSeek酱 · 活人感与服务器压力测试</title><style>', css, '</style><main>',
            '<h1>DeepSeek酱 · 活人感与服务器压力测试</h1>', f'<p>{escape(when)} · 合成连续对话 · 原模型真实调用 · 无微信发送</p>',
            '<p class="banner">' + summary + '</p><div class="cards">',
            f'<div class="card"><strong>36轮 / {requests}次</strong><small>完整保留全部回放与修复请求</small></div>',
            f'<div class="card"><strong>68.54 MiB</strong><small>候选100条压力进程峰值RSS</small></div>',
            f'<div class="card"><strong>0 次发送微信</strong><small>没有部署或改生产配置</small></div></div>',
            '<h2>先看结论</h2>', bullets(findings), '<h2>测试方式</h2>', bullets(method),
            '<h2>服务器压力</h2>', table(['指标', '线上代码基线 / 100条', '候选 / 100条'], resources),
            bullets(limits[:5]), '<h2>回复耗时</h2><p>7组主回放；每格依次为样本数 / p50 / p95。这里测到的是隔离处理耗时，不能当作手机端送达速度。</p>',
            table(['类别', '基线', '候选'], latency_rows), '<h2>调用与缓存</h2>',
            f'<p>包含原始与重复试验的全部39次请求，合计 {tokens:,} Token。没有新增逐轮拟人化审稿；额外请求来自原有格式恢复。</p>',
            table(['指标', '基线', '候选'], token_rows), bullets(limits[5:]),
            '<h2>完整主回放</h2><p>甲乙在各场景中独立打乱，先隐藏版本身份。文字气泡与表情动作按真实输出顺序展示；没有删掉不理想的句子。</p>',
            '<button onclick="document.body.classList.toggle(\'revealed\')">显示 / 隐藏版本身份</button>',
            '<nav class="nav">' + ''.join(f'<a href="#{case}">{escape(next(p["title"] for p in pairs if p["case"] == case))}</a>' for case in CASES) + '</nav>']
    md = ['# DeepSeek酱活人感与服务器压力测试', '', summary, '', '## 关键发现', '',
          *['- ' + x for x in findings], '', '## 方法与范围', '', *['- ' + x for x in method], '',
          '## 资源结果', '', md_table(['指标', '基线100条', '候选100条'], resources), '',
          *['- ' + x for x in limits], '', '## 主回放耗时', '',
          '每格依次为样本数 / p50 / p95；不含实际收消息和微信发送。', '',
          md_table(['类别', '基线', '候选'], latency_rows), '', '## 全部请求统计', '',
          md_table(['指标', '基线', '候选'], token_rows), '', f'合计 {requests} 次、{tokens:,} Token。', '', '## 全部主回放', '']
    for pair in pairs:
        case = pair['case']
        html.extend([f'<section class="scene" id="{case}"><h3>{escape(pair["title"])}</h3>',
                     '<p class="note">' + escape(NOTES[case]) + '</p><div class="duo">'])
        md.extend(['### ' + pair['title'], '', NOTES[case], ''])
        for variant in pair['variants']:
            arm = mapping[case + '/' + variant['label']]
            data = read(primary_path(case, arm))
            html.append(conversation(data, variant['label'], arm))
            md.append(transcript_md(data, f'{variant["label"]} · {ARM_NAMES[arm]}'))
        html.append('</div></section>')
    html.extend(['<h2>保留的首次试验片段</h2><p>下面是别人互聊与点头收尾的首次运行，已在主回放补测。保留原句，避免只展示较好的重复结果。</p>'])
    md.extend(['## 保留的首次试验', '', '主回放之外的8轮重复试验，未删除不理想输出。', ''])
    for case in ('others', 'resolved'):
        html.extend(['<details><summary>' + escape(case) + ' · 首次</summary><div class="duo">'])
        for arm in ARM_NAMES:
            row = read(RUNS / f'{case}-{arm}/result.json')
            html.append(conversation(row, ARM_NAMES[arm], arm))
            md.append(transcript_md(row, case + ' · 首次 · ' + ARM_NAMES[arm]))
        html.append('</div></details>')
    html.extend(['<h2>仍需改进</h2>', bullets(next_steps),
                 '<footer>资料来源：本次隔离result.json、usage回执、进程采样、阶段健康检查。报告由本地脚本生成；不含私人聊天或凭据。测试脚本、源码快照、匿名评价与映射分别留存。</footer></main></html>'])
    md.extend(['## 后续重点', '', *['- ' + x for x in next_steps], '',
               '机器可读数据：artifacts/focus-evaluation-20261105/metrics-summary.json；回放原始结果位于同目录results/runs。', ''])
    html_path = OUT / '聊天回放与压力测试.html'
    md_path = ROOT / 'CHAT_NATURALNESS_PRESSURE_TEST_20261105.md'
    html_path.write_text(''.join(html), 'utf-8')
    md_path.write_text('\n'.join(md), 'utf-8')
    print(json.dumps({'html': str(html_path), 'markdown': str(md_path), 'requests': requests,
                      'tokens': tokens, 'primary_pairs': len(pairs)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
