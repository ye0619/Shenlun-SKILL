#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
已废止口径残留检查器（shenlun-judge 维护工具）

用途：每次修订 skill 后跑一次，检出**已废止口径特征串的残留**，把"冲突检测报告"
从人工专项变成例行检查（体检报告 2026-10-03 第六节第 3 条）。

用法：
  python 工具/lint_rulings.py                                  # 扫默认三处
  python 工具/lint_rulings.py --root .dsh/skills/shenlun-judge  # 只扫指定目录（可多次）
  python 工具/lint_rulings.py --show-suppressed                # 连"废止说明上下文/误报豁免"一起列出
  python 工具/lint_rulings.py --json                           # 机器可读（落盘留痕）
  python 工具/lint_rulings.py --selftest                       # 运行自检（回归用例）

默认扫描根（存在才扫；可用 --root 多次指定覆盖）：
  .dsh/skills/shenlun-judge      (skill 本体)
  templates/workspace            (分发模板)
  批改规范                        (工作区现用副本)

规则表（id / 正则 / 说明）：
  L01  ±10%                旧字数弹性（现为"X 字左右"±50）
  L02  ⌈超出               旧字数扣分公式（`⌈超出达标上限 ÷ 50⌉`，已废）
  L03  60–70               旧公文三池比例（现为 内容 80%／格式 10%／语言 10%）
  L04  分三层              旧"0 家点三层口径"（现为"先判重要性"）
  L05  0 家一律            旧"0 家一律不计分／一律进提醒区"
  L06  两个配套脚本 / 配套脚本只有 / 只有 `count_chars` 与   旧脚本数（实为 4 个）
  L07  3＋3＋4              旧分值池取值（10 分/3 点＝40%，已废）
  L08  每点 ≥3 点           误述（实文为"每题至少 3 个点"）
  L09  119 KB / 61 KB      过期体积标注（现为 140 KB / 70 KB）
  L10  (?<!通用)点数可行区间  旧措辞（现为"通用可行区间 3 ≤ N ≤ 10"）
  L11  空喊口号             旧示例措辞（现为"通篇空泛表态而无事实/机制/论证支撑"）

允许上下文（suppression，四类）：
  ① 命中行**本身或其前一行**含
     已废｜废止｜⛔｜~~｜修订记录｜补注｜修正｜历史｜旧口径｜被取代｜2026-09-19｜2026-09-22｜2026-09-25｜2026-09-27
  ② 命中行的**后 1–3 行**含 `补注｜修正`（如紧跟其后的"（2026-10-03 补注：…）"）
  ③ 命中行含"旧→新"对照（`→`）且其**紧邻上一段**是 `**2026-xx-xx …修订…**` 批次标题（中间最多隔 2 个空行）
  ④ **误报豁免（负向上下文，只作用于 L05／L11）**：命中行本身就是**在禁止这件事**
     （含 不得再写｜禁止｜不得｜禁用话术｜⛔）或**描述性/引用性**地使用该旧措辞
     （含 不空喊口号｜正面描述｜描述性用法｜解释性引用｜引用性）时，也记为 **suppressed**
     （真残留行内没有这些标志，照旧报出；见 `NEGATIVE_HINTS`）
记为 **suppressed**（默认不报，不计入残留数；`--show-suppressed` 时单列并标出忽略理由）。
—— 这些行本就是"废止说明/历史记录/禁语条目"，报出来是噪声；真正要抓的是**没有废止标注的旧句**。

输出：<相对路径>:<行号>: [L07] <说明> :: <行内容（截断 100 字符）>
     结尾统计：残留 N 项；已忽略（废止说明／误报豁免）M 项
     `--show-suppressed` 时每条忽略项后附忽略理由
     `--json` 的 suppressed 项新增 `reason` 字段（已有键不变）

退出码：0 = 无残留；1 = 有残留（需人工确认）；2 = 参数错误。
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 规则表

RULES = [
    ('L01', r'±\s*10\s*%', '旧字数弹性 ±10%（已改为"X 字左右"±50）'),
    ('L02', r'⌈超出', '旧字数扣分公式（⌈超出达标上限 ÷ 50⌉，2026-09-27 已废）'),
    ('L03', r'60\s*[–\-—]\s*70', '旧公文三池比例 60–70（现为 内容 80%／格式 10%／语言 10%）'),
    ('L04', r'分三层', '旧"0 家点三层口径"（现为"先判重要性：重要→弱共识计分"）'),
    ('L05', r'0\s*家一律', '旧"0 家一律不计分／一律进提醒区"（现为先判重要性）'),
    ('L06', r'两个配套脚本|配套脚本只有|只有\s*`count_chars`\s*与',
     '旧脚本数（实为 4 个：count_chars / 卷面核验 / split_reference / ref_independence）'),
    ('L07', r'3＋3＋4', '旧分值池取值（10 分/3 点＝40%，破 35% 与题型 30% 上限，已废）'),
    ('L08', r'每点\s*≥\s*3\s*点', '误述（实文为"每题至少 3 个点"，见 small-questions.md §4）'),
    ('L09', r'119 KB|61 KB', '过期体积标注（现为 scoring-standards 140 KB／essay-judging 70 KB）'),
    ('L10', r'(?<!通用)点数可行区间', '旧措辞（现为"通用可行区间 3 ≤ N ≤ 10"，且须叠加题型上限）'),
    ('L11', r'空喊口号', '旧示例措辞（现为"通篇空泛表态而无事实/机制/论证支撑"）'),
]

COMPILED = [(rid, re.compile(pat), desc) for rid, pat, desc in RULES]

# ① 命中行本身或前一行含这些词 = "废止说明/历史记录"上下文 → 忽略
#    （`~~` 亦计入：Markdown 删除线＝该段被废止，见 scoring-standards 第六批修订）
SUPPRESS_RE = re.compile(
    r'已废|废止|⛔|~~|修订记录|补注|修正|历史|旧口径|被取代'
    r'|2026-09-19|2026-09-22|2026-09-25|2026-09-27'
)

# ② 命中行的后 1–3 行含"补注/修正" = 紧随其后的注记 → 忽略
SUPPRESS_NEXT_RE = re.compile(r'补注|修正')
NEXT_LOOKAHEAD = 3

# ③ 批次条目：紧邻上一段是 `**2026-xx-xx …修订…**` 批次标题（中间最多隔 BATCH_GAP 个空行），
#    且本行是"旧→新"对照（含 `→`）→ 忽略（历史对照条目，不是未标注的旧句）。
#    只跨空行、不许跨正文段落，故窗口极窄，不会放过真正的旧句。
BATCH_HEADING_RE = re.compile(r'^\*\*\s*20\d\d-\d\d-\d\d[^\n]*?(修订|变更|废止|新增|回补)')
CONTRAST_RE = re.compile(r'→')
BATCH_GAP = 2

# ④ 误报豁免（负向上下文）：只对"特征串本身就是被点名批评/被正面描述"的规则生效。
#    命中行同时含"我就是在禁止这件事"或"这是描述性/引用性用法"的标志时不报。
#    真残留（如 `不合格＝空喊口号、通篇复述材料、编造无据事实。`）行内没有这些标志，照旧报出。
NEGATIVE_HINTS = {
    'L05': [
        (re.compile(r'不得再写|禁止|不得|禁用话术|⛔'), '负向上下文：禁语条目/纠正说明'),
    ],
    'L11': [
        (re.compile(r'不得再写|禁止|不得|禁用话术|⛔'), '负向上下文：禁语条目/纠正说明'),
        (re.compile(r'不空喊口号|正面描述|描述性用法|解释性引用|引用性'),
         '负向上下文：描述性/引用性用法'),
    ],
}

DEFAULT_ROOTS = [
    os.path.join('.dsh', 'skills', 'shenlun-judge'),
    os.path.join('templates', 'workspace'),
    '批改规范',
]

TRUNC = 100


# ---------------------------------------------------------------- 扫描

def collect_md(root):
    """递归收集 root 下所有 *.md，返回排序后的绝对路径列表。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for name in filenames:
            if name.lower().endswith('.md'):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def default_roots(cwd):
    return [os.path.normpath(os.path.join(cwd, r)) for r in DEFAULT_ROOTS]


def pick_base(roots, cwd):
    """选一个可读的相对基准：各扫描根的公共父目录（单个根时取其父目录）。"""
    real = [os.path.normpath(r) for r in roots]
    if not real:
        return cwd
    try:
        base = os.path.commonpath(real)
    except ValueError:      # 跨盘符等异常
        return cwd
    if base in real:        # 只有一个根（或某根恰是公共路径）时再上一级
        parent = os.path.dirname(base)
        if parent:
            base = parent
    return base


def relpath(path, base):
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        return path
    if rel.startswith('..'):
        return path
    return rel.replace('\\', '/')


def line_context(lines, idx):
    """命中行是否处于"废止说明/历史记录/批次条目"上下文；返回 (理由, 命中词) 或 None。"""
    line = lines[idx]
    m = SUPPRESS_RE.search(line)
    if m:
        return ('本行废止标注（「%s」）' % m.group(0), m.group(0))
    if idx > 0:
        m = SUPPRESS_RE.search(lines[idx - 1])
        if m:
            return ('前一行废止标注（「%s」）' % m.group(0), m.group(0))
    for k in range(1, NEXT_LOOKAHEAD + 1):
        j = idx + k
        if j >= len(lines):
            break
        m = SUPPRESS_NEXT_RE.search(lines[j])
        if m:
            return ('后 %d 行注记（「%s」）' % (k, m.group(0)), m.group(0))
    if CONTRAST_RE.search(line):
        j, blanks = idx - 1, 0
        while j >= 0 and not lines[j].strip():
            blanks += 1
            j -= 1
        if j >= 0 and blanks <= BATCH_GAP:
            m = BATCH_HEADING_RE.match(lines[j].strip())
            if m:
                return ('批次条目（标题「%s」＋本行旧→新对照）' % m.group(0)[:24], m.group(0)[:24])
    return None


def rule_false_positive(rid, line):
    """规则级"误报豁免"；返回 (理由, 命中词) 或 None。"""
    for rx, why in NEGATIVE_HINTS.get(rid, ()):
        m = rx.search(line)
        if m:
            return ('%s（「%s」）' % (why, m.group(0)), m.group(0))
    return None


def scan_text(text, rel):
    """扫描一段文本，返回 (residues, suppressed)，元素为 dict（selftest 亦走这里）。"""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    residues, suppressed = [], []
    for idx, line in enumerate(lines):
        hits = [(rid, desc) for rid, rx, desc in COMPILED if rx.search(line)]
        if not hits:
            continue
        ctx = line_context(lines, idx)           # 整行级：废止说明/历史记录/批次条目
        text_out = line.strip()[:TRUNC]
        for rid, desc in hits:
            item = {
                'file': rel,
                'line': idx + 1,
                'rule': rid,
                'desc': desc,
                'text': text_out,
            }
            mark = ctx or rule_false_positive(rid, line)   # 后者是 L05/L11 的负向上下文豁免
            if mark:
                item['reason'] = mark[0]
                item['suppressed_by'] = mark[1]
                suppressed.append(item)
            else:
                residues.append(item)
    return residues, suppressed


def scan_file(path, base):
    """返回 (residues, suppressed)，元素为 dict。"""
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as fh:
        content = fh.read()
    return scan_text(content, relpath(path, base))


def scan(roots, cwd):
    base = pick_base(roots, cwd)
    residues, suppressed, files = [], [], 0
    for root in roots:
        if not os.path.isdir(root):
            print('[提示] 跳过不存在的目录：%s' % relpath(root, base), file=sys.stderr)
            continue
        for path in collect_md(root):
            files += 1
            r, s = scan_file(path, base)
            residues.extend(r)
            suppressed.extend(s)
    order = {'file': 0, 'line': 1, 'rule': 2}
    residues.sort(key=lambda d: (d['file'], d['line'], d['rule']))
    suppressed.sort(key=lambda d: (d['file'], d['line'], d['rule']))
    return residues, suppressed, files


# ---------------------------------------------------------------- 输出

def render(items, tag):
    out = []
    for it in items:
        line = '%s:%d: [%s] %s :: %s' % (it['file'], it['line'], it['rule'], it['desc'], it['text'])
        if tag:
            why = it.get('reason') or ('命中「%s」' % it.get('suppressed_by', ''))
            line += '  <忽略：%s>' % why
        out.append(line)
    return out


def selftest():
    """自检：规则命中 / 废止上下文（本行・前一行・后 1–3 行・批次标题＋旧→新）/ 误报豁免（L05・L11）
    / 真残留必须报出。逐项打印 OK｜FAIL，末尾给"项数"。"""
    # (说明, 文本行, 期望规则, 期望结局 'residue'｜'suppressed')
    cases = [
        ('命中：旧字数弹性', ['±10% 的旧弹性'], 'L01', 'residue'),
        ('本行废止标注', ['> ⛔ 废止 ±10% 的旧弹性'], 'L01', 'suppressed'),
        ('命中：旧分值池取值', ['| 10 分 | 3 | 3＋3＋4 |'], 'L07', 'residue'),
        ('行内"已废"', ['原"3＋3＋4"＝40% 已废'], 'L07', 'suppressed'),
        ('命中：旧脚本数', ['配套脚本只有两个'], 'L06', 'residue'),
        ('命中：每点 ≥3 点误述', ['每点 ≥3 点应改为每题至少 3 个点'], 'L08', 'residue'),
        ('命中：旧措辞点数可行区间', ['点数可行区间 3 ≤ N ≤ 10'], 'L10', 'residue'),
        ('本行废止标注', ['> ⛔ 废止旧措辞"点数可行区间"'], 'L10', 'suppressed'),
        ('前一行废止标注', ['> ⛔ 已废止的旧口径如下：', '分三层处理'], 'L04', 'suppressed'),
        ('删除线 ~~ 视为废止（回归#4）',
         ['~~**用户作答字数由"不扣分"改为"核验并计分"**——`扣分 = ⌈超出达标上限的字数 ÷ 50⌉`~~'],
         'L02', 'suppressed'),
        ('后第 2 行"补注"（回归#7）',
         ['"10 分题只能 3 点"。现口径：**点数可行区间 3 ≤ N ≤ 10**',
          '（每点 ≥总分×10% ⇒ N ≤ 10；每点 ≤总分×35% ⇒ N ≥ 3）；',
          '**（2026-10-03 补注：以上"均可行"是通用区间结论。）**'],
         'L10', 'suppressed'),
        ('批次标题＋旧→新对照（回归#6）',
         ['**2026-09-22 修订（内置默认国考标准；用户裁定）**', '',
          '1. **公文三池权重：60–70%／15–20%／15–20% → `内容 80%／格式 10%／语言 10%`**；'],
         'L03', 'suppressed'),
        ('负向豁免·禁语条目（回归#5）',
         ['**"不得再写\'0 家一律不计分／一律进提醒区\'"**。'], 'L05', 'suppressed'),
        ('负向豁免·禁语条目（回归#2）',
         ['⚠ **不得再写"0 家一律不计分／一律进提醒区"**（判语黑名单 #12）'], 'L05', 'suppressed'),
        ('负向豁免·描述性用法（回归#1）',
         ['| ⑤ **落点具体度** | 观点是否落到具体对象 / 机制 / 主体（不空喊口号） | —— |'],
         'L11', 'suppressed'),
        ('负向豁免·引用性用法（回归#3）',
         ['缺失不扣分）；② **描述性**（范文特征卡/画像里"不空喊口号"的正面描述）。'],
         'L11', 'suppressed'),
        ('真残留必须报出（2024浙江A:53）',
         ['并能回扣主旨；不合格＝空喊口号、通篇复述材料、编造无据事实。'], 'L11', 'residue'),
        ('L05 真残留仍报出（无禁语标志）',
         ['机构 0 家一律不计分、直接进提醒区。'], 'L05', 'residue'),
        ('真残留必须报出（2026浙江A:48）',
         ['**不合格**＝空喊口号、通篇复述材料、编造无据事实。**注意：仅复述材料不是硬伤、不封顶**，'
          '只在内容与论证维度体现。'], 'L11', 'residue'),
        ('批次标题不能单独豁免（须有旧→新）',
         ['**2026-09-22 修订（内置默认国考标准）**', '', '1. **公文三池权重原为 60–70%**；'],
         'L03', 'residue'),
        ('旧→新对照无批次标题仍报出',
         ['1. **公文三池权重：60–70%／15–20%／15–20% → 新口径**；'], 'L03', 'residue'),
        ('后 3 行无注记不得豁免',
         ['点数可行区间 3 ≤ N ≤ 10', '（每点 ≥总分×10% ⇒ N ≤ 10）；', '表中"常用点数"只是常规选择。'],
         'L10', 'residue'),
    ]
    checks, failed = 0, 0

    def check(ok, msg):
        nonlocal checks, failed
        checks += 1
        if not ok:
            failed += 1
        print('%s %s' % ('OK  ' if ok else 'FAIL', msg))

    for why, block, rid, want in cases:
        residues, suppressed = scan_text('\n'.join(block), '<selftest>')
        got = ('residue' if any(d['rule'] == rid for d in residues)
               else 'suppressed' if any(d['rule'] == rid for d in suppressed)
               else 'miss')
        extra = ''
        if got == 'suppressed':
            extra = '  <忽略：%s>' % next(d['reason'] for d in suppressed if d['rule'] == rid)
        check(got == want, '%-30s %-3s %-10s%s' % (why, rid, got, extra))

    # L10 负向：带"通用"前缀不应命中
    text = '**通用可行区间 3 ≤ N ≤ 10**'
    hits = [rid for rid, rx, _ in COMPILED if rx.search(text)]
    check('L10' not in hits, '%-30s %-3s %-10s' % ('L10 负向："通用可行区间"不该命中', 'L10', ','.join(hits) or '-'))

    print('selftest: 共 %d 项，%s' % (checks, '全部通过' if failed == 0 else '%d 项失败' % failed))
    return 1 if failed else 0


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')       # Windows 控制台打印中文
    except Exception:
        pass

    ap = argparse.ArgumentParser(description='已废止口径残留检查器（shenlun-judge 维护工具）')
    ap.add_argument('--root', action='append', default=None,
                    help='扫描根目录，可多次指定（默认扫 skill 本体／templates/workspace／批改规范）')
    ap.add_argument('--json', action='store_true', help='输出 JSON（便于落盘记录）')
    ap.add_argument('--show-suppressed', action='store_true',
                    help='同时列出被"废止说明上下文"忽略的命中')
    ap.add_argument('--selftest', action='store_true', help='运行自检')
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    cwd = os.getcwd()
    roots = [os.path.normpath(os.path.join(cwd, r)) for r in args.root] if args.root \
        else default_roots(cwd)

    residues, suppressed, files = scan(roots, cwd)
    base = pick_base(roots, cwd)

    if args.json:
        payload = {
            'residues': residues,
            'suppressed': suppressed,
            'counts': {
                'residues': len(residues),
                'suppressed': len(suppressed),
                'files': files,
                'rules': len(RULES),
            },
            'roots': [relpath(r, base) for r in roots],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if residues else 0

    print('=== 已废止口径残留检查（%d 个目录 / %d 个 .md / %d 条规则）==='
          % (len(roots), files, len(RULES)))
    for r in roots:
        mark = '' if os.path.isdir(r) else '  [不存在，已跳过]'
        print('  扫描：%s%s' % (relpath(r, base), mark))
    print('')

    for line in render(residues, tag=False):
        print(line)
    if residues:
        print('')
    if args.show_suppressed:
        print('--- 以下命中位于"废止说明/历史记录/禁语条目"上下文，默认忽略 ---')
        for line in render(suppressed, tag=True):
            print(line)
        print('')

    print('残留 %d 项；已忽略（废止说明／误报豁免）%d 项' % (len(residues), len(suppressed)))
    if residues:
        print('→ 请人工确认：这些旧句是否缺少废止标注；确认后在 skill 内改写为现行口径。')
    return 1 if residues else 0


if __name__ == '__main__':
    sys.exit(main())
