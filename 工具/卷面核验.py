#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整卷字数一次过核验（shenlun-judge 专用；字数为「只核验、不计分」口径）

用途：**一次调用**把一份整卷作答按题切块、逐题给出达标判定，输出 **ASCII 表格**（控制台不会乱码），
并可同时写出报告用的中文 markdown 块。取代"写一堆 .dsh 临时文件 → 反复跑 count_chars.py → 再回读"的搬沙流程。

字数口径（唯一出处 references/word-count.md）：
  * 2026-09-27 用户裁定：**用户作答字数不计入评分**——超限/偏少只作提示，不扣分、不降档。
  * 达标判定："不超过 XX"→ 上限 XX；"XX 字左右"→ **XX±50**；区间题按区间；"不少于 N"按下限。

用法：
  python 工具/卷面核验.py 作答记录/浙江/2019浙江A.txt --limits 300,300,300,1200
  python 工具/卷面核验.py 作答.txt --spec "1:300,2:approx300,3:1000-1200,4:min800"
  python 工具/卷面核验.py 作答.txt --limits 300,300,300,1200 --md 批改报告/.字数核验/x-字数.md
  python 工具/卷面核验.py 作答.txt --limits 300,300 --json
  python 工具/卷面核验.py --selftest

--spec 语法（逗号分隔，按题序）：
  1:300            不超过 300 字
  2:300-500        区间题（1000—1200 这种）
  3:approx1000     "1000 字左右"（±50）
  4:min800         "不少于 800 字"
  5:-              该题无字数要求（只统计）

退出码：0 = 统计完成（结论可能是 OVER/SHORT，看结论列，**不是报错**）；
        2 = 参数错误；3 = 文件不存在。
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import count_chars as cc          # 同一目录的字数统计引擎（唯一口径来源）
except ImportError:                    # pragma: no cover
    cc = None

APPROX = 50                            # "X 字左右" 的达标半径（用户口径 2026-09-15 立、09-21 重申）
NOTE = '字数不计入评分（用户 2026-09-27 裁定）：超限/偏少只作提示，不扣分、不降档。'


def parse_spec(spec):
    """'1:300,2:approx300' -> {1: {'kind':'cap','v':300}, ...}"""
    out = {}
    for i, raw in enumerate(spec.replace('，', ',').split(','), start=1):
        item = raw.strip()
        if not item:
            continue
        if ':' in item:
            head, _, body = item.partition(':')
            idx = int(head.strip()) if head.strip().isdigit() else i
            body = body.strip()
        else:
            idx, body = i, item
        if body in ('-', '', 'none'):
            out[idx] = {'kind': 'none'}
        elif body.startswith('approx'):
            out[idx] = {'kind': 'approx', 'v': int(body[6:])}
        elif body.startswith('min'):
            out[idx] = {'kind': 'min', 'v': int(body[3:])}
        elif '-' in body:
            lo, _, hi = body.partition('-')
            out[idx] = {'kind': 'range', 'lo': int(lo), 'hi': int(hi)}
        else:
            out[idx] = {'kind': 'cap', 'v': int(body)}
    return out


def limits_to_spec(limits):
    return ','.join('%d:%d' % (i, v) for i, v in enumerate(limits, start=1))


def judge(total, rule):
    """返回 (verdict, band_text, detail_zh)。verdict ∈ OK/OVER/SHORT/NA"""
    k = rule.get('kind', 'none') if rule else 'none'
    if k == 'none':
        return 'NA', '-', '题干未给字数要求，仅统计'
    if k == 'cap':
        v = rule['v']
        band = '<=%d' % v
        if total > v:
            return 'OVER', band, '超出题干上限 %d 字' % (total - v)
        return 'OK', band, '达标（不超过 %d 字）' % v
    if k == 'approx':
        v = rule['v']
        lo, hi = v - APPROX, v + APPROX
        band = '%d-%d' % (lo, hi)
        if total > hi:
            return 'OVER', band, '超出达标区上限 %d 字' % (total - hi)
        if total < lo:
            return 'SHORT', band, '低于达标区下限 %d 字' % (lo - total)
        return 'OK', band, '达标（%d 字左右，达标区 %d-%d）' % (v, lo, hi)
    if k == 'range':
        lo, hi = rule['lo'], rule['hi']
        band = '%d-%d' % (lo, hi)
        if total > hi:
            return 'OVER', band, '超出区间上限 %d 字' % (total - hi)
        if total < lo:
            return 'SHORT', band, '低于区间下限 %d 字' % (lo - total)
        return 'OK', band, '达标（区间内）'
    if k == 'min':
        v = rule['v']
        band = '>=%d' % v
        if total < v:
            return 'SHORT', band, '少于要求下限 %d 字' % (v - total)
        return 'OK', band, '达标（不少于 %d 字）' % v
    return 'NA', '-', '未知要求'


def ascii_table(rows):
    """rows: [{'q':1,'label':'Q1','chars':354,'band':'<=300','verdict':'OVER','delta':54}]"""
    out = []
    out.append('=== char-count check (answer-card metric, incl. punctuation; NOT scored) ===')
    out.append('%-3s %-14s %6s %-12s %-10s %s' % ('Q', 'label', 'chars', 'req', 'band', 'verdict'))
    for r in rows:
        verdict = r['verdict']
        if verdict in ('OVER', 'SHORT') and r['delta']:
            verdict = '%s%+d' % (verdict, r['delta'])
        out.append('%-3s %-14s %6d %-12s %-10s %s'
                   % (r['q'] or '-', r['label'][:14], r['chars'], r['req'], r['band'], verdict))
    over = [r['q'] for r in rows if r['verdict'] == 'OVER']
    short = [r['q'] for r in rows if r['verdict'] == 'SHORT']
    out.append('SUMMARY: over=%s short=%s (计数不为扣分, 只作提示)' % (over or '-', short or '-'))
    return '\n'.join(out)


def md_block(rows, command):
    lines = ['### 用户作文字数（只核验，**不计入评分**）', '',
             '| 题 | 字数 | 题干要求 | 达标判定 |', '|---|---|---|---|']
    for r in rows:
        if r['verdict'] == 'NA':
            verdict = '题干未给字数要求（仅统计）'
        elif r['verdict'] == 'OK':
            verdict = r['detail']
        else:
            verdict = '⚠ %s（**按用户口径不计分**）' % r['detail']
        lines.append('| %s | %d 字 | %s | %s |' % (r['label'], r['chars'], r['req'], verdict))
    lines += ['', '> 核验命令：`%s`' % command,
              '> 口径：%s' % NOTE]
    return '\n'.join(lines) + '\n'


def selftest():
    import tempfile
    sample = (
        '第1题\n' + '甲' * 354 + '\n\n'
        '第2题\n' + '乙' * 95 + '\n\n'
        '第3题\n' + '丙' * 305 + '\n\n'
        '第4题\n' + '丁' * 1038 + '\n')
    cases = []
    tmp = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8')
    try:
        tmp.write(sample)
        tmp.close()
        secs = cc.split_sections(sample)
        cases.append(('split into 4 questions', len(secs) == 4, 'got %d' % len(secs)))
        rules = parse_spec('1:300,2:min100,3:approx300,4:1000-1200')
        got = []
        for i, sec in enumerate(secs, start=1):
            stat = cc.analyse_text(sec['text'], 'x')
            v, band, _d = judge(stat['total'], rules[i])
            got.append((stat['total'], v))
        cases.append(('Q1 354 > 300 -> OVER', got[0] == (354, 'OVER'), str(got[0])))
        cases.append(('Q2 95 < min 100 -> SHORT', got[1] == (95, 'SHORT'), str(got[1])))
        cases.append(('Q3 305 within 300+/-50 -> OK', got[2] == (305, 'OK'), str(got[2])))
        cases.append(('Q4 1038 within 1000-1200 -> OK', got[3] == (1038, 'OK'), str(got[3])))
        # "X 字左右" 必须是 ±50，不是 ±10%
        cases.append(('approx 1050 -> OK', judge(1050, {'kind': 'approx', 'v': 1000})[0] == 'OK', ''))
        cases.append(('approx 1051 -> OVER', judge(1051, {'kind': 'approx', 'v': 1000})[0] == 'OVER', ''))
        cases.append(('approx 949 -> SHORT', judge(949, {'kind': 'approx', 'v': 1000})[0] == 'SHORT', ''))
        cases.append(('--limits shorthand parsed', parse_spec(limits_to_spec([300, 200]))[2] == {'kind': 'cap', 'v': 200}, ''))
    finally:
        os.unlink(tmp.name)
    failed = 0
    for name, ok, extra in cases:
        if not ok:
            failed += 1
        print('%s %s %s' % ('OK  ' if ok else 'FAIL', name, extra))
    print('selftest: %s' % ('all passed' if failed == 0 else '%d failed' % failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description='整卷字数一次过核验（只核验，不计分）')
    ap.add_argument('file', nargs='?', help='整卷作答文本（.txt/.md）')
    ap.add_argument('--limits', help='按题序的逗号分隔上限，如 300,300,300,1200')
    ap.add_argument('--spec', help='比 --limits 更强的写法：1:300,2:approx300,3:1000-1200,4:min800')
    ap.add_argument('--labels', help='题号标签，逗号分隔；默认 第1题,第2题…')
    ap.add_argument('--md', help='把报告用的中文 markdown 块写到该路径（UTF-8）')
    ap.add_argument('--json', action='store_true', help='输出 JSON（ASCII 安全）')
    ap.add_argument('--selftest', action='store_true', help='运行自检')
    args = ap.parse_args()

    if cc is None:
        print('ERROR: 找不到同目录的 count_chars.py（字数统计引擎）', file=sys.stderr)
        return 2
    if args.selftest:
        return selftest()
    if not args.file:
        ap.print_help()
        return 2
    if not os.path.exists(args.file):
        print('ERROR: file not found: %s' % args.file, file=sys.stderr)
        return 3
    if not (args.limits or args.spec):
        print('ERROR: 需要 --limits 或 --spec', file=sys.stderr)
        return 2

    try:
        rules = parse_spec(args.spec) if args.spec else parse_spec(limits_to_spec(
            [int(x) for x in args.limits.replace('，', ',').split(',') if x.strip()]))
    except ValueError:
        print('ERROR: --limits/--spec 格式不对（示例：--limits 300,300,1200 或 --spec "1:300,2:approx1000"）',
              file=sys.stderr)
        return 2

    labels = [x.strip() for x in args.labels.replace('，', ',').split(',')] if args.labels else []
    with open(args.file, 'r', encoding='utf-8-sig') as fh:
        raw = fh.read()

    sections = cc.split_sections(raw)
    if not sections:
        sections = [{'name': labels[0] if labels else os.path.basename(args.file),
                     'text': raw, 'matched': True}]

    rows, qi = [], 0
    for sec in sections:
        stat = cc.analyse_text(sec['text'], args.file)
        if sec['matched']:
            qi += 1
            rule = rules.get(qi, {'kind': 'none'})
        else:
            rule = {'kind': 'none'}
        verdict, band, detail = judge(stat['total'], rule)
        k = rule.get('kind')
        if k == 'cap':
            req = '<=%d' % rule['v']
            delta = stat['total'] - rule['v'] if stat['total'] > rule['v'] else 0
        elif k == 'approx':
            req = '~%d(+/-%d)' % (rule['v'], APPROX)
            delta = stat['total'] - (rule['v'] + APPROX) if stat['total'] > rule['v'] + APPROX \
                else ((rule['v'] - APPROX) - stat['total'] if stat['total'] < rule['v'] - APPROX else 0)
        elif k == 'range':
            req = '%d-%d' % (rule['lo'], rule['hi'])
            delta = stat['total'] - rule['hi'] if stat['total'] > rule['hi'] else \
                (rule['lo'] - stat['total'] if stat['total'] < rule['lo'] else 0)
        elif k == 'min':
            req = '>=%d' % rule['v']
            delta = rule['v'] - stat['total'] if stat['total'] < rule['v'] else 0
        else:
            req, delta = '-', 0
        label = labels[qi - 1] if (sec['matched'] and qi - 1 < len(labels)) else sec['name']
        if sec['matched'] and qi - 1 >= len(labels):
            label = 'Q%d' % qi   # 默认题号用 ASCII，避免控制台中文乱码
        rows.append({'q': qi if sec['matched'] else 0, 'label': label, 'chars': stat['total'],
                     'req': req, 'band': band, 'verdict': verdict, 'delta': delta, 'detail': detail})

    command = 'python 工具/卷面核验.py %s --spec "%s"' % (
        args.file, args.spec or limits_to_spec([rules[i]['v'] for i in sorted(rules) if rules[i].get('v')]))

    if args.json:
        print(json.dumps({'file': args.file, 'note': NOTE, 'rows': rows}, ensure_ascii=True, indent=2))
    else:
        print(ascii_table(rows))
        if args.md:
            os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
            with open(args.md, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(md_block(rows, command))
            print('MD_WRITTEN: %s' % args.md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
