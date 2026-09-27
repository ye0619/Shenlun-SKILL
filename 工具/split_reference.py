#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参考答案汇总件 → 按题拆分（shenlun-judge 专用）

用途：把一份"多机构答案汇总"的整份文本**一次调用**拆成 `第N题.txt`，落到
`参考答案/{省份}/{卷ID}/`，取代过去那套"写一次性 .py 脚本 → 手工重排落盘"的做法
（历史实测：21 个临时脚本、14,321 字符，写完即删；每次都重写一遍同样的逻辑）。

纪律：
  * **只新增，不删源件**：源汇总件保持原样留在原地（建议自行移入 `.原始件/`）。
  * 目标文件已存在时**默认跳过**并告警；`--force` 才覆盖。
  * 拆分只按题号标题切分，**正文一字不改**（不重排、不改写、不合并同源）。

用法：
  python 工具/split_reference.py "参考答案/浙江/2026浙江B/.原始件/x.md" --outdir "参考答案/浙江/2026浙江B"
  python 工具/split_reference.py 汇总件.txt --outdir out/ --dry-run        # 只看会切出什么
  python 工具/split_reference.py 汇总件.txt --outdir out/ --labels         # 每块附带识别到的来源标签
  python 工具/split_reference.py --selftest

兼容的题号写法：`第三大题`、`第1题：…`、`## 第一题`、`**第二题**`、`答题纸 第一大题`、
`2019年浙江省公考《申论》题（A卷） 第二大题`。正文句子不会被误切。

退出码：0 = 完成；2 = 参数错误；3 = 文件不存在。**若切出的题块 < 2，返回 4**（多半是格式不同，
此时请改用助手手工切分，不要盲信脚本输出）。
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import count_chars as cc
except ImportError:                    # pragma: no cover
    cc = None

CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
QNUM_RE = re.compile(r'第([一二三四五六七八九十百0-9]{1,3})')


def q_index(name):
    """从标题里取出题号整数；取不到返回 None。支持 第十一题 / 第11题。"""
    m = QNUM_RE.search(name or '')
    if not m:
        return None
    tok = m.group(1)
    if tok.isdigit():
        return int(tok)
    if tok == '十':
        return 10
    if tok.startswith('十'):
        return 10 + CN_NUM.get(tok[1:], 0)
    if tok.endswith('十'):
        return CN_NUM.get(tok[0], 0) * 10
    if '十' in tok:
        a, _, b = tok.partition('十')
        return CN_NUM.get(a, 0) * 10 + CN_NUM.get(b, 0)
    return CN_NUM.get(tok)


def source_labels(text):
    """粗识别块内的来源标签（机构名/公众号名），仅供登记参考，不参与拆分。"""
    known = ['粉笔', '华图', '中公', '站长', '四海飞扬', '展鸿', '永岸', '申倩', '贺冲',
             '小马哥', '月月申论', '胡荣展', '隐隐于斯', '导氮', '步知', '上岸', '半月谈']
    hits = []
    for k in known:
        if k in text:
            hits.append(k)
    return hits


def plan(raw):
    """返回 [(qindex, name, text), ...]；无法分节时返回 []"""
    if cc is None:
        return []
    sections = cc.split_sections(raw)
    out = []
    for i, sec in enumerate(sections):
        if not sec.get('matched'):
            continue
        out.append([q_index(sec['name']), sec['name'], sec['text']])
    # 题号缺失时按出现顺序补号
    used = set(x[0] for x in out if x[0])
    nxt = 1
    for item in out:
        if item[0] is None:
            while nxt in used:
                nxt += 1
            item[0] = nxt
            used.add(nxt)
    return out


def selftest():
    import tempfile
    cases = []
    samples = {
        'md-h2': ('# title\n\n## 第一题\n要求：不超过300字。\n\n### 参考答案\n甲甲甲\n\n'
                  '## 第二题\n要求：不超过600字。\n\n### 参考答案\n乙乙乙\n', [1, 2]),
        'paper-prefix': ('答题纸 第一大题\n甲甲甲\n\n'
                         '2019年浙江省公考《申论》题（A卷） 第二大题\n乙乙乙\n', [1, 2]),
        'plain': ('第三大题\n丙丙丙\n第四大题\n丁丁丁\n', [3, 4]),
        'prose-not-split': ('第一题\n甲甲\n结合给定资料，谈谈第二题怎么做。\n乙乙\n', []),
    }
    failed = 0
    with tempfile.TemporaryDirectory() as d:
        for name, (text, expect) in samples.items():
            got = [x[0] for x in plan(text)]
            ok = (got == expect)
            if not ok:
                failed += 1
            print('%s plan[%-16s] expect=%s got=%s' % ('OK ' if ok else 'FAIL', name, expect, got))
        # 落盘：写文件、跳过已存在、--force 覆盖
        text = samples['md-h2'][0]
        items = plan(text)
        src = os.path.join(d, 'src.md')
        with open(src, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
        written = write_out(items, d, force=False, dry=False)
        got = sorted(os.listdir(d))
        cases.append(('wrote Q1/Q2 txt', sorted(set(got) & {'第1题.txt', '第2题.txt'}) == ['第1题.txt', '第2题.txt'], str(got)))
        again = write_out(items, d, force=False, dry=False)
        cases.append(('existing files skipped', again == [], str(again)))
        forced = write_out(items, d, force=True, dry=False)
        cases.append(('--force overwrites', len(forced) == len(written), str(forced)))
        cases.append(('qnum 第十一题 -> 11', q_index('第十一题') == 11, str(q_index('第十一题'))))
        cases.append(('qnum 第12题 -> 12', q_index('第12题') == 12, str(q_index('第12题'))))
    for name, ok, extra in cases:
        if not ok:
            failed += 1
        print('%s %-24s %s' % ('OK ' if ok else 'FAIL', name, extra))
    print('selftest: %s' % ('all passed' if failed == 0 else '%d failed' % failed))
    return 1 if failed else 0


def write_out(items, outdir, force, dry):
    written = []
    os.makedirs(outdir, exist_ok=True)
    for idx, name, text in items:
        path = os.path.join(outdir, '第%d题.txt' % idx)
        body = text.strip('\n') + '\n'
        if os.path.exists(path) and not force:
            print('SKIP   exists: %s' % path)
            continue
        if not dry:
            with open(path, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(body)
        written.append((path, len(body)))
        print('%-6s %s  chars=%d' % ('PLAN' if dry else 'WRITE', path, len(body)))
    return written


def main():
    ap = argparse.ArgumentParser(description='参考答案汇总件按题拆分（只新增，不动源件）')
    ap.add_argument('source', nargs='?', help='汇总件路径（.txt/.md）')
    ap.add_argument('--outdir', help='输出目录，如 参考答案/浙江/2019浙江A')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的 第N题.txt')
    ap.add_argument('--dry-run', action='store_true', help='只打印会写什么，不写盘')
    ap.add_argument('--labels', action='store_true', help='每块附带识别到的来源标签（登记参考）')
    ap.add_argument('--selftest', action='store_true', help='运行自检')
    args = ap.parse_args()

    if cc is None:
        print('ERROR: 找不到同目录的 count_chars.py', file=sys.stderr)
        return 2
    if args.selftest:
        return selftest()
    if not args.source or not args.outdir:
        ap.print_help()
        return 2
    if not os.path.exists(args.source):
        print('ERROR: file not found: %s' % args.source, file=sys.stderr)
        return 3

    with open(args.source, 'r', encoding='utf-8-sig') as fh:
        raw = fh.read()

    items = plan(raw)
    if len(items) < 2:
        print('ERROR: 只识别到 %d 个题块，未达 2 个 —— 该汇总件格式与脚本预期不同，'
              '请按 SKILL.md 第三节第 1 步人工切分（不要盲信脚本输出）。' % len(items))
        return 4

    print('SOURCE: %s (%d chars)' % (args.source, len(raw)))
    print('PLAN  : %d questions -> %s' % (len(items), args.outdir))
    for idx, name, text in items:
        tail = ''
        if args.labels:
            hits = source_labels(text)
            tail = '  labels=%s' % (','.join(hits) if hits else '-')
        print('  Q%-3d from=%-24s chars=%d%s' % (idx, name[:24], len(text), tail))
    written = write_out(items, args.outdir, force=args.force, dry=args.dry_run)
    print('SUMMARY: written=%d skipped_or_planned=%d source_untouched=yes'
          % (len(written), len(items) - len(written)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
