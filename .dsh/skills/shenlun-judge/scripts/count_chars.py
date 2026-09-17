#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
申论字数核验器（shenlun-judge 唯一字数统计工具）

主要用途：**核验示例答案/满分答案/范文的字数不超上限**（这是本 skill 反复出错的地方），
同时用于统计用户作答字数。用确定性脚本取代模型目测估算。

用法：
  python count_chars.py 示例答案.txt --limit 300          # 小题示例答案（上限 300 字）
  python count_chars.py 作文.txt --limit 1000 --approx     # "1000 字左右"
  python count_chars.py 作文.txt --limit 1200 --floor 1000 # "1000—1200 字"
  python count_chars.py 整卷作答.txt --split --limits 300,200,300,500,500   # 套卷切块统计
  python count_chars.py 答案.txt --limit 300 --json        # 机器可读，便于落盘留痕
  python count_chars.py --selftest

口径说明见 ../references/word-count.md：
  答题卡口径 = 全部非空白字符（标点、数字、字母、序号各计 1 格）
               剔除空格、换行，以及助手转写/排版时添加的 markdown 标记
  另剔除以【说明】【批注】【备注】【转写说明】等开头的说明段（到空行或文末）——
  这类文字是背景说明，不是答题内容，不占答题卡格子。
纯汉字数、去行首序号数作为辅助口径一并输出。

退出码：0 = 统计完成（结论可能是"超出/偏少"，请看结论行，非报错）；2 = 参数错误；3 = 文件不存在。
"""

import argparse
import json
import os
import re
import sys
import unicodedata

CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3005\u3007]')
FENCE_RE = re.compile(r'^[ \t]*```[^\n]*$', re.M)
LINK_RE = re.compile(r'!?\[([^\]\n]*)\]\([^)\n]*\)')
MD_LINE_HEAD_RE = re.compile(r'^[ \t]*#{1,6}[ \t]*', re.M)
MD_QUOTE_RE = re.compile(r'^[ \t]*>[ \t]?', re.M)
MD_BULLET_RE = re.compile(r'^[ \t]{0,3}[-*+][ \t]+', re.M)
NUMBERING_RE = re.compile(
    r'^[ \t]*(?:\d{1,3}[.、)）]|[（(][ \t]*\d{1,3}[ \t]*[)）]|[一二三四五六七八九十]{1,3}[、.)）])[ \t]*',
    re.M)
MD_MARKS_RE = re.compile(r'[*`~_]')
MD_TABLE_SEP_RE = re.compile(r'^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$', re.M)
MD_PIPE_RE = re.compile(r'\|')
SECTION_RE = re.compile(
    r'^(?:第[一二三四五六七八九十百0-9]{1,3}(?:大题|小题|题)[^\n]{0,20}'
    r'|题目[一二三四五六七八九十0-9]{1,3}[^\n]{0,20}'
    r'|题[目]?[一二三四五六七八九十0-9]{1,3}[：:、][^\n]{0,20})$')
ANNOTATION_BLOCK_RE = re.compile(
    r'^[ \t]*(?:【|\[)(?:说明|批注|备注|转写说明|转写情况|作答说明|补充说明)(?:】|\])')
ANNOTATION_TOKEN_RE = re.compile(
    r'(?:【|\[)(?:作答|答案|转写存疑|此处略|下略|约\d+字|待补|缺\d+字)(?:】|\])'
    r'|（(?:转写存疑|此处略|下略|约\d+字)）')


def strip_annotation_blocks(text):
    """删除【说明】【批注】【备注】【转写说明】等开头的说明段（到空行或文末）。

    这类段落是用户/助手写的背景说明，不是答题内容，不应计入答题卡字数。
    返回 (净化后的文本, 删除的说明段数)。
    """
    out, removed, skipping = [], 0, False
    for ln in text.split('\n'):
        if ANNOTATION_BLOCK_RE.match(ln):
            skipping = True
            removed += 1
            continue
        if skipping:
            if not ln.strip():          # 空行 = 说明段结束
                skipping = False
            continue
        out.append(ln)
    return '\n'.join(out), removed


def strip_markdown(text):
    """剔除助手转写/排版时添加的 markdown 标记与说明段，只保留真实书写内容。"""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text, _removed = strip_annotation_blocks(text)
    text = ANNOTATION_TOKEN_RE.sub('', text)
    text = FENCE_RE.sub('', text)
    text = LINK_RE.sub(r'\1', text)
    text = MD_LINE_HEAD_RE.sub('', text)
    text = MD_QUOTE_RE.sub('', text)
    text = MD_BULLET_RE.sub('', text)
    text = MD_TABLE_SEP_RE.sub('', text)
    text = MD_PIPE_RE.sub('', text)
    text = MD_MARKS_RE.sub('', text)
    return text


def count_bucket(ch):
    if CJK_RE.match(ch):
        return 'hanzi'
    cat = unicodedata.category(ch)
    if cat.startswith('P') or cat.startswith('S'):
        return 'punct'
    if cat.startswith('N') or cat.startswith('L'):
        return 'alnum'
    return 'other'


def analyse(path):
    with open(path, 'r', encoding='utf-8-sig') as fh:
        raw = fh.read()
    return analyse_text(raw, path)


def analyse_text(raw, path):
    cleaned = strip_markdown(raw)
    _ann_text, ann_removed = strip_annotation_blocks(
        raw.replace('\r\n', '\n').replace('\r', '\n'))
    body = ''.join(ch for ch in cleaned if not ch.isspace())
    no_numbering = ''.join(ch for ch in NUMBERING_RE.sub('', cleaned) if not ch.isspace())

    buckets = {'hanzi': 0, 'punct': 0, 'alnum': 0, 'other': 0}
    for ch in body:
        buckets[count_bucket(ch)] += 1

    md_residue = bool(
        re.search(r'\*\*|^\s*```', raw, re.M) or
        re.search(r'^\s*\|.*\|\s*$', raw, re.M) or
        re.search(r'^\s*>', raw, re.M)
    )
    return {
        'file': path,
        'total': len(body),
        'hanzi': buckets['hanzi'],
        'punct': buckets['punct'],
        'alnum': buckets['alnum'],
        'other': buckets['other'],
        'no_numbering': len(no_numbering),
        'md_residue': md_residue,
        'annotations_removed': ann_removed,
    }


def split_sections(raw):
    """按"第X大题/第X题"等小题标号把整份作答切成若干块。

    返回 [{'name','text','matched'}, ...]；matched=False 表示卷首导语/尾注等非作答块，
    不占用分题字数上限。匹配到的标号少于 2 个时返回 []（调用方按整篇处理）。
    """
    lines = raw.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out, cur, cur_matched, buf = [], None, False, []
    for ln in lines:
        m = SECTION_RE.match(ln.strip())
        if m and len(ln.strip()) <= 40:
            if cur is not None:
                out.append({'name': cur, 'text': '\n'.join(buf), 'matched': cur_matched})
            cur, cur_matched, buf = m.group(0).strip(), True, []
        else:
            if cur is None:
                if not ln.strip():
                    continue
                cur, cur_matched = '卷首/导语', False
            buf.append(ln)
    if cur is not None:
        out.append({'name': cur, 'text': '\n'.join(buf), 'matched': cur_matched})
    return out if len([s for s in out if s['matched']]) >= 2 else []


def judge(total, limit, floor, approx):
    """返回 (结论标签, 结论文字, 建议区间文字)"""
    if limit is None:
        return 'info', '未提供字数要求，仅输出统计值', '—'
    if floor is not None:
        low, high = floor, limit
        span_txt = '%d–%d 字' % (low, high)
        if total < low:
            return 'short', '低于区间下限 %d 字（差 %d 字）' % (low, low - total), span_txt
        if total > high:
            return 'over', '超出区间上限 %d 字' % (total - high), span_txt
        return 'ok', '在题目要求区间内', span_txt
    if approx:
        low, high = int(round(limit * 0.9)), int(round(limit * 1.1))
        span_txt = '%d–%d 字（"X 字左右"±10%%）' % (low, high)
        if total < low:
            return 'short', '低于弹性下限 %d 字（差 %d 字）' % (low, low - total), span_txt
        if total > high:
            return 'over', '超出弹性上限 %d 字' % (total - high), span_txt
        return 'ok', '在"X 字左右"弹性范围内', span_txt
    lo_ok, hi_ok = int(round(limit * 0.85)), int(limit * 0.95)
    span_txt = '示例答案目标区间 %d–%d 字（上限 %d 字的 85%%–95%%）' % (lo_ok, hi_ok, limit)
    if total > limit:
        return 'over', '超出上限 %d 字（超出部分阅卷可能不看，必须压缩）' % (total - limit), span_txt
    if total > hi_ok:
        return 'tight', ('未超上限但高于目标区间上沿 %d 字（建议再压到 %d 字以内，留出腾挪空间）'
                         % (total - hi_ok, hi_ok)), span_txt
    if total < lo_ok:
        return 'short', '偏少（距目标区间下限还差 %d 字，可再补要点）' % (lo_ok - total), span_txt
    return 'ok', '达标（落在目标区间 %d–%d 字内）' % (lo_ok, hi_ok), span_txt


TAG_MARK = {'over': '[超出]', 'tight': '[贴限]', 'ok': '[达标]', 'short': '[偏少]', 'info': '[统计]'}


def report(stat, limit, floor, approx, label):
    tag, msg, span = judge(stat['total'], limit, floor, approx)
    name = label or os.path.basename(stat['file'])
    lines = []
    lines.append('=== %s ===' % name)
    lines.append('答题卡口径字数（含标点/数字/字母/序号，不含空格换行与 markdown 标记）：%d' % stat['total'])
    lines.append('  构成：汉字 %d ／ 标点 %d ／ 数字字母 %d ／ 其他 %d'
                 % (stat['hanzi'], stat['punct'], stat['alnum'], stat['other']))
    lines.append('  辅助口径：纯汉字 %d ／ 去行首序号 %d' % (stat['hanzi'], stat['no_numbering']))
    if limit is not None:
        lines.append('字数要求：%s' % span)
        pct = 100.0 * stat['total'] / limit
        label_pct = '占目标值比' if (floor is not None or approx) else '占上限比'
        lines.append('%s：%.1f%%（基准 %d 字）' % (label_pct, pct, limit))
    lines.append('%s %s' % (TAG_MARK[tag], msg))
    if stat['md_residue']:
        lines.append('[提示] 原文含 markdown 标记（**、表格、引用、代码块）：已按净化口径统计；'
                     '示例答案请写成纯文本后再核验。')
    if stat.get('annotations_removed'):
        lines.append('[提示] 已剔除 %d 段【说明】类说明文字（不计入作答字数）。'
                     % stat['annotations_removed'])
    lines.append('结论标记：%s' % tag)
    return '\n'.join(lines), tag, msg


def selftest():
    cases = [
        ('你好，世界。', 6),
        ('一、加强监管。', 7),
        ('一、加强监管。\n二、优化服务。', 14),
        ('**加粗**文本', 4),
        ('| a | b |\n|---|---|\n| 中 | 文 |', 4),
        ('```\n代码块内容\n```\n正文', 7),
        ('【说明】这是我的背景说明，不算作答。\n\n正文内容。', 5),
        ('【批注】随便写点\n\n你好。\n\n后面的正文。', 9),
        ('第一句。（转写存疑）第二句。', 8),
    ]
    failed = 0
    for text, expect in cases:
        cleaned = strip_markdown(text)
        got = len(''.join(ch for ch in cleaned if not ch.isspace()))
        flag = 'OK ' if got == expect else 'FAIL'
        if got != expect:
            failed += 1
        print('%s %-24r expect=%d got=%d' % (flag, text, expect, got))
    print('selftest: %s' % ('all passed' if failed == 0 else '%d failed' % failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description='申论字数核验器（答题卡口径）')
    ap.add_argument('files', nargs='*', help='待统计的 UTF-8 文本文件（一个或多个）')
    ap.add_argument('--limit', type=int, help='题目字数上限或目标值，如 300 / 1000 / 1200')
    ap.add_argument('--limits', help='分题上限，逗号分隔（配合 --split），如 300,200,500,500,300')
    ap.add_argument('--floor', type=int, help='题目给出的下限，如 "1000—1200字" 的 1000')
    ap.add_argument('--approx', action='store_true', help='题目为 "X 字左右"（±10% 弹性）')
    ap.add_argument('--split', action='store_true',
                    help='按 "第X大题/第X题" 标号把整份作答切块后逐题统计（套卷用）')
    ap.add_argument('--label', help='本次统计的名称，如 "第一题作答"')
    ap.add_argument('--json', action='store_true', help='输出 JSON（便于落盘记录）')
    ap.add_argument('--selftest', action='store_true', help='运行自检')
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.files:
        ap.print_help()
        return 2

    per_limits = None
    if args.limits:
        try:
            per_limits = [int(x) for x in args.limits.replace('，', ',').split(',') if x.strip()]
        except ValueError:
            print('--limits 需为逗号分隔的整数，如 300,200,500', file=sys.stderr)
            return 2

    out = []
    worst = None
    rank = {'over': 3, 'tight': 2, 'short': 1, 'ok': 0, 'info': -1}

    for path in args.files:
        if not os.path.exists(path):
            print('文件不存在：%s' % path, file=sys.stderr)
            return 3
        with open(path, 'r', encoding='utf-8-sig') as fh:
            raw = fh.read()

        if args.split or per_limits:
            sections = split_sections(raw)
            if not sections:
                print('[提示] %s 未找到 ≥2 个"第X大题/第X题"标号，按整篇统计。' % path)
                sections = [{'name': args.label or os.path.basename(path), 'text': raw, 'matched': True}]
            qi = 0
            for sec in sections:
                stat = analyse_text(sec['text'], path)
                stat['section'] = sec['name']
                if sec['matched']:
                    if per_limits:
                        limit = per_limits[qi] if qi < len(per_limits) else args.limit
                    else:
                        limit = args.limit
                    qi += 1
                else:
                    limit = None
                stat['limit_used'] = limit
                text, tag, msg = report(stat, limit, args.floor, args.approx,
                                        '%s · %s' % (args.label or os.path.basename(path), sec['name']))
                stat['verdict'] = tag
                stat['verdict_text'] = msg
                stat['rendered'] = text
                out.append(stat)
                if worst is None or rank[tag] > rank[worst]:
                    worst = tag
            continue

        stat = analyse(path)
        stat['limit_used'] = args.limit
        text, tag, msg = report(stat, args.limit, args.floor, args.approx, args.label)
        stat['verdict'] = tag
        stat['verdict_text'] = msg
        stat['rendered'] = text
        out.append(stat)
        if worst is None or rank[tag] > rank[worst]:
            worst = tag

    if args.json:
        for stat in out:
            stat.pop('rendered', None)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for stat in out:
            print(stat['rendered'])
            print('')
    return 0


if __name__ == '__main__':
    sys.exit(main())
