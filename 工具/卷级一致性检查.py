#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卷级一致性检查：一条命令核"一卷的批改产物是否齐备、登记是否到位"。

背景（2026-10-11 立）
--------------------
近期批改暴露的主要缺陷**不是"某题判错"，而是卷级产物缺件或未登记**：
报告写了但 `批改报告/README.md` 没有登记行（实测 2018 国考副省级、2019 国考地市级、
2023 浙江 B 三卷缺行）；小题规范／共识表／字数核验／刻度三件套散落各处，人工回忆容易漏；
参考答案家数与共识表声明不一致时无人发现。本脚本把这些**可机器判定**的项一次过核完，
输出缺失清单与非 0 退出码，供 `SKILL.md` 9.2 收尾自检调用。

用法
----
    python 工具/卷级一致性检查.py --paper 2019国考地市级 --questions 1,2 --essay 0 --reference-questions 1,2
    python 工具/卷级一致性检查.py --paper 2023浙江C --province 浙江 --questions 1,2,3 --essay 3 --reference-questions 1,2 --exemplars available --sample-essay available
    python 工具/卷级一致性检查.py --paper 2019国考地市级 --json
    python 工具/卷级一致性检查.py --selftest

约定（与 workspace-layout.md / report-template.md 一致）
-------------------------------------------------------
    报告         批改报告/{省份}/{卷ID}-批改报告.md（重批为 {卷ID}-批改报告-v2.md；兼容旧式 {卷ID}-v2-批改报告.md）
    小题规范     批改规范/{省份}/{卷ID}-第N题-动态评分规范.md
    大作文规范   批改规范/{省份}/{卷ID}-大作文批改规范.md
    共识表       批改报告/.采分点共识/{卷ID}-共识表-全卷.md
    字数核验     批改报告/.字数核验/{卷ID}-字数.md
    刻度三件套   批改报告/.大作文刻度/{卷ID}-{梯队画像,特征卡,素材清单}.md
    登记行       批改报告/README.md 内出现该卷ID

按 rulings.md §1.3 条件建档：1—2 题免建过程文件；3 题及以上检查本次规范，
有参考答案的小题／公文检查共识表；本次含大作文且有可用范文才检查刻度。
题型、参考答案、可比范文与可照录的本题完整范文由调用方分别核实后显式传入，不猜最后一题为大作文。
报告内展示按 §1.3.1 逐题检查，不受1—2题独立建档豁免影响；固定栏目与表头见 report-template.md。
检查范围为结构及数值完整性，证据真实性、候选完整覆盖和范文全文完整性仍须回源核对。
退出码：0＝齐备（或仅 ⚠ 提示）；1＝有 ✗ 缺件；2＝条件未核实、参数或环境错误。
本脚本只读文件、不改任何内容；家数比对为经验口径（L4），仅供参考。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "1.5.0（2026-10-26）"

OK, WARN, MISS, SKIP, UNKNOWN = "ok", "warn", "miss", "skip", "unknown"
MARK = {OK: "✓", WARN: "⚠", MISS: "✗", SKIP: "○", UNKNOWN: "?"}
SUPERSEDED_MARKER = "<!-- shenlun-rule-status: superseded -->"


# ----------------------------------------------------------------- 单项检查
def _check_exists(root: Path, rel: str, title: str, results: list, level=MISS):
    p = root / rel
    if p.is_file() and p.stat().st_size:
        with p.open(encoding="utf-8", errors="replace") as source:
            if SUPERSEDED_MARKER in source.read(4096):
                results.append((level, title, "规则已过期，须按现行口径重建：%s" % rel))
                return False
        results.append((OK, title, "%s（%d 字节）" % (rel, p.stat().st_size)))
        return True
    results.append((level, title, "缺文件或文件为空：%s" % rel))
    return False


def _report_paths(root: Path, province: str, paper: str):
    d = root / "批改报告" / province
    if not d.is_dir():
        return []
    filename = re.compile(re.escape(paper) +
                          r"(?:-批改报告(?:-v[1-9]\d*)?|-v[1-9]\d*-批改报告)\.md")
    matches = (p for p in d.iterdir() if p.is_file() and filename.fullmatch(p.name))
    return sorted(matches,
                  key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def _questions_from_report(text: str):
    """从报告正文里取题号（`### 第N题（…）`）。"""
    return sorted({int(m) for m in re.findall(r"^#{2,4}\s*第\s*(\d+)\s*题", text, re.M)})


def _visible_markdown(text):
    """模板示例、注释不能充当报告正文。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def _heading_sections(text, pattern):
    heads = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*#*\s*$", text, re.M))
    sections = []
    for i, head in enumerate(heads):
        match = re.match(pattern, head.group(2))
        if not match:
            continue
        end = next((h.start() for h in heads[i + 1:]
                    if len(h.group(1)) <= len(head.group(1))), len(text))
        sections.append((match, text[head.end():end]))
    return sections


def _cell_value(cell):
    """还原行内代码、粗体及强调的文字，保留代码内的字面字符。"""
    code_spans = []

    def protect_code(hit):
        value = hit[2]
        if value.startswith(" ") and value.endswith(" ") and value.strip():
            value = value[1:-1]
        code_spans.append(value)
        return "\x00%d\x00" % (len(code_spans) - 1)

    value = re.sub(r"(?<![\\`])(`+)(?!`)(.+?)(?<!`)\1(?!`)", protect_code, cell.strip())
    patterns = (r"(?<![\\*])(\*{1,3})(?=\S)(.+?)(?<=\S)\1(?!\*)",
                r"(?<![\w\\_])(_{1,3})(?=\S)(.+?)(?<=\S)\1(?![\w_])")
    while True:
        previous = value
        for pattern in patterns:
            value = re.sub(pattern, lambda hit: hit[2], value)
        if value == previous:
            break
    value = re.sub(r"\\([`*_])", lambda hit: hit[1], value)
    return re.sub(r"\x00(\d+)\x00", lambda hit: code_spans[int(hit[1])], value).strip()


def _filled(cell):
    value = _cell_value(cell)
    return (bool(value) and not re.search(r"\{[^}]*\}|待填写|待补|占位|TODO", value, re.I)
            and not re.fullmatch(r"(?:…+|\.{3,}|[-—–]+|同上)(?:[（(].*)?", value))


def _table_rows(body, required):
    """只接受有表头、分隔行和实质数据行的内嵌 Markdown 表。"""
    lines = body.splitlines()
    def cells(line):
        return [_cell_value(c.replace(r"\|", "|"))
                for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
    for i in range(len(lines) - 1):
        if not lines[i].lstrip().startswith("|"):
            continue
        headers, separator = cells(lines[i]), cells(lines[i + 1])
        if (not set(required).issubset(headers) or len(headers) != len(separator)
                or len(set(headers)) != len(headers)
                or not all(re.fullmatch(r":?-{3,}:?", c) for c in separator)):
            continue
        rows = []
        for line in lines[i + 2:]:
            if not line.lstrip().startswith("|"):
                break
            values = cells(line)
            if len(values) != len(headers) or not all(_filled(v) for v in values):
                return []
            rows.append(dict(zip(headers, values)))
        return rows
    return []


def _argument_rows(body):
    """论点表列序固定；至少三列分论点，更多时须连续扩列。"""
    rows = _table_rows(body, ("来源", "题目", "总论点", "分论点1", "分论点2", "分论点3"))
    if not rows:
        return []
    headers = list(rows[0])
    expected = ["来源", "题目", "总论点"] + ["分论点%d" % n for n in range(1, len(headers) - 2)]
    return rows if headers == expected else []


def _numeric_score(value):
    hit = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*[-–—~～]\s*(\d+(?:\.\d+)?))?\s*/\s*(\d+(?:\.\d+)?)", value)
    if not hit:
        return None
    low, high, maximum = float(hit[1]), float(hit[2] or hit[1]), float(hit[3])
    return (low, high, maximum) if 0 <= low <= high <= maximum and maximum > 0 else None


def _absence_record(body, status):
    fields = {}
    for line in body.splitlines():
        hit = re.match(r"^\s*[-*]?\s*(状态|已检查|原因)\s*[：:]\s*(.+)$", line)
        if hit:
            fields[hit[1]] = _cell_value(hit[2])
    return (fields.get("状态") == status
            and all(_filled(fields.get(key, "")) for key in ("已检查", "原因")))


def _has_prose(body):
    for line in body.splitlines():
        value = re.sub(r"^\s*>\s?", "", line).strip()
        value = re.sub(r"^(?:[-*+]\s+|\d+[.)、]\s*)", "", value)
        if not value or re.match(r"^\s*(?:#|\|)", value):
            continue
        if re.match(r"^(?:范文ID|来源|标签|字数|模拟档位|选取理由|状态|已检查|原因|正文字数核验|正文覆盖)\s*[：:]", value):
            continue
        if re.fullmatch(r"!?\[[^]]*\]\([^)]+\)", value) or re.match(r"^(?:见|详见|参见|请见|https?://)", value):
            continue
        if _filled(value):
            return True
    return False


def _check_report_content(text, questions, essay, references, exemplars, sample_essay="unknown"):
    """按题核正文；独立缓存不能代替表格，少量题目同样检查。"""
    results = []
    visible = _visible_markdown(text)
    sections = _heading_sections(visible, r"第\s*([1-9]\d*)\s*题(?:\s|[（(:：]|$)")
    if essay is None:
        results.append((UNKNOWN, "报告题型条件", "须显式传 --essay N；无大作文传 0"))
    if references is None and any(q != essay for q in questions):
        results.append((UNKNOWN, "报告参考答案条件", "须核实并传 --reference-questions；无则填 none"))
    if essay in questions and exemplars == "unknown":
        results.append((UNKNOWN, "报告范文条件", "须核实并传 --exemplars available 或 none"))
    if essay in questions and sample_essay == "unknown":
        results.append((UNKNOWN, "报告示例全文条件", "须独立核实可照录的本题完整范文并传 --sample-essay available 或 none"))

    for q in questions:
        matches = [body for hit, body in sections if int(hit[1]) == q]
        if len(matches) != 1:
            results.append((MISS, "第%d题报告正文" % q, "题目正文缺失或题目标题重复"))
            continue
        body = matches[0]

        def section(title):
            found = _heading_sections(body, re.escape(title) + r"(?:\s|[（(:：]|$)")
            return found[0][1] if len(found) == 1 else ""

        def record(title, valid, detail):
            results.append((OK if valid else MISS, "第%d题报告·%s" % (q, title),
                            "正文已核对" if valid else detail))

        if essay is None:
            continue
        if q != essay:
            points = _table_rows(section("采分点表"),
                                 ("采分点", "材料依据", "作答原文", "命中状态", "分值", "得分理由"))
            record("采分点表", bool(points), "缺内嵌采分点表、必需列或实质数据行")
            answer = section("参考答案")
            answer_bodies = _heading_sections(answer, r"参考正文(?:\s|[（(:：]|$)")
            if answer_bodies:
                answer = answer_bodies[0][1] if len(answer_bodies) == 1 else ""
            record("参考答案", _has_prose(answer), "缺本题参考答案正文；标题、链接或占位不算正文")
            if references is not None:
                consensus = section("参考答案共识表")
                if q in references:
                    rows = _table_rows(consensus, ("候选要点", "材料依据", "来源覆盖", "重要性与理由", "计分状态", "分值"))
                    record("共识表", bool(rows), "缺内嵌共识表、必需列或实质数据行；附件路径不能替代")
                else:
                    record("共识表", _absence_record(consensus, "无可用参考答案"),
                           "须在共识表栏目登记无可用参考答案、已检查路径和原因")
            continue

        scale, scores, arguments, exemplar = (section(t) for t in ("大作文刻度表", "范文逐篇评分", "论点表", "示例范文"))
        scale_ids, score_ids, reference_types = [], [], {}
        if exemplars == "none":
            for title, content in (("刻度表", scale), ("范文逐篇评分", scores), ("论点表", arguments)):
                record(title, _absence_record(content, "无可用完整可比范文"),
                       "须保留栏目并登记无可用完整可比范文、已检查路径和原因")
        elif exemplars == "available":
            scale_rows = _table_rows(scale, ("范文ID", "来源", "参照类型", "原题依据", "可比与不可比维度",
                                            "题意", "内容与论证", "结构", "语言", "相对位置与局限"))
            score_rows = _table_rows(scores, ("范文ID", "来源", "模拟得分", "模拟档位", "评分依据（含原文）"))
            scale_ids = [row["范文ID"] for row in scale_rows]
            score_ids = [row["范文ID"] for row in score_rows]
            reference_types = {row["范文ID"]: row["参照类型"] for row in scale_rows}
            record("刻度表", bool(scale_rows) and len(set(scale_ids)) == len(scale_ids)
                   and all(kind in ("同题", "异题弱参照") for kind in reference_types.values()),
                   "缺内嵌刻度表、原题依据、可比与不可比维度等必需列或实质数据行，或范文ID重复、参照类型不符")
            numeric = [_numeric_score(row["模拟得分"]) for row in score_rows]
            same_question_maxima = {score[2] for row, score in zip(score_rows, numeric)
                                    if score and reference_types.get(row["范文ID"]) == "同题"}
            valid = (bool(score_rows) and all(numeric) and len(set(score_ids)) == len(score_ids)
                     and len(same_question_maxima) <= 1)
            record("范文逐篇评分", valid,
                   "每篇须有唯一ID、来源、数值得分/原题满分、档位及原文依据；异题满分可不同，同题满分须一致，各篇数值或区间须在0至自身满分内")
            coverage = (bool(scale_rows) and bool(score_rows) and set(scale_ids) == set(score_ids)
                        and {r["范文ID"]: r["来源"] for r in scale_rows}
                        == {r["范文ID"]: r["来源"] for r in score_rows})
            record("范文评分覆盖", coverage, "刻度表与逐篇评分表的范文ID／来源未一一对应")
            argument_rows = _argument_rows(arguments)
            record("论点表", bool(argument_rows),
                   "缺本题论点表或实质单元格；列序须为来源、题目、总论点、分论点1至3，更多分论点连续扩列")
            argument_sources = [re.fullmatch(r"([\w.-]+)\s*·\s*(.+)", row["来源"])
                                for row in argument_rows]
            argument_pairs = [(hit[1], hit[2].strip()) for hit in argument_sources if hit]
            argument_coverage = (bool(argument_rows) and bool(scale_rows) and bool(score_rows)
                                 and len(argument_pairs) == len(argument_rows)
                                 and len({pair[0] for pair in argument_pairs}) == len(argument_rows)
                                 and dict(argument_pairs) == {r["范文ID"]: r["来源"] for r in scale_rows}
                                 == {r["范文ID"]: r["来源"] for r in score_rows})
            record("论点表覆盖", argument_coverage,
                   "论点表须逐篇覆盖刻度及评分表，来源写作“范文ID · 来源”，不得漏篇、重复ID或错配来源")
        if sample_essay == "none":
            record("示例范文", _absence_record(exemplar, "无可照录的本题完整范文"),
                   "须保留示例范文栏目，独立登记无可照录的本题完整范文、已检查路径和原因")
        elif sample_essay == "available":
            selected = re.search(r"范文ID\s*[：:]\s*([\w.-]+)(?=\s|[（(]|$)", _cell_value(exemplar))
            record("示例范文", bool(selected) and selected[1] in scale_ids
                   and selected[1] in score_ids and reference_types.get(selected[1]) == "同题"
                   and _has_prose(exemplar),
                   "缺范文全文或入选范文ID未在刻度表和评分表中登记；示例全文须为同题候选，不能用异题弱参照")
    return results


def _consensus_paths(root: Path, paper: str, questions):
    """允许合卷表或按题建表；只取本次范围，不借用其他题的表。"""
    directory = root / "批改报告" / ".采分点共识"
    whole = directory / ("%s-共识表-全卷.md" % paper)
    paths = [whole] if whole.is_file() else []
    whole_text = whole.read_text(encoding="utf-8") if paths else ""
    for q in questions:
        p = directory / ("%s-第%d题-共识表.md" % (paper, q))
        if p.is_file() and not re.search(r"^#{1,6}\s*第\s*%d\s*题\b" % q, whole_text, re.M):
            paths.append(p)
    return paths


def _consensus_counts(root: Path, paper: str, questions):
    """共识表里每题声明的"独立来源 N 家"数字。

    按 `## 第N题` 切段，**每段只取第一处"独立来源 N 家"**——共识表 §2 之后往往还会
    引用脚本实测值或修复前后的对照（如"修复后独立来源 16 家"），取第一处才等于
    该题的人工清点结论（2026-10-11 修正：此前取全文所有匹配，会把对照值也当成声明值）。
    """
    paths = _consensus_paths(root, paper, questions)
    if not paths:
        return None, []
    text = "\n".join(p.read_text(encoding="utf-8") for p in paths)
    heads = list(re.finditer(r"^##\s*第\s*(\d+)\s*题", text, re.M))
    pairs = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        seg = text[m.end():end]
        hit = re.search(r"独立来源\s*(\d+)\s*家", seg)
        if hit:
            pairs.append((int(m.group(1)), int(hit.group(1))))
    if not pairs:  # 退路：旧格式（无 `## 第N题` 分段）
        pairs = [(-1, int(n)) for n in re.findall(r"独立来源\s*(\d+)\s*家", text)]
    return paths[0], pairs


def _question_list(value):
    if value.strip().lower() == "none":
        return []
    if not re.fullmatch(r"[1-9]\d*(?:,[1-9]\d*)*", value):
        raise argparse.ArgumentTypeError("题号须为逗号分隔的正整数（如 1,2,3），无参考答案填 none")
    return sorted({int(q) for q in value.split(",")})


def _script_counts(root: Path, province: str, paper: str):
    """跑 ref_independence.py，取每题"独立来源 N 家"。"""
    script = root / "工具" / "ref_independence.py"
    refdir = root / "参考答案" / province / paper
    if not script.is_file() or not refdir.is_dir():
        return None
    try:
        out = subprocess.run(
            [sys.executable, str(script), str(refdir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(root), timeout=300,
        ).stdout or ""
    except Exception as exc:                                    # pragma: no cover
        return [("error", str(exc))]
    pairs = re.findall(r"===\s*第\s*(\d+)\s*题（.*?独立来源\s*(\d+)\s*家）", out)
    if not pairs:
        return []
    return [(int(q), int(n)) for q, n in pairs]


# ----------------------------------------------------------------- 主检查
def check_paper(root: Path, province: str, paper: str, essay=None, run_counts=True,
                questions=None, reference_questions=None, exemplars="unknown", report=None,
                sample_essay="unknown"):
    r = []
    # 1) 报告（允许 v2 变体）
    reports = ([root / report] if report else _report_paths(root, province, paper))
    reports = [p for p in reports if p.is_file()]
    text = ""
    if reports:
        rel = reports[0].relative_to(root).as_posix()
        text = reports[0].read_text(encoding="utf-8", errors="replace")
        extra = "；另有 %s" % "、".join(
            p.name for p in reports[1:]) if len(reports) > 1 else ""
        r.append((OK, "批改报告", "%s（%d 字节）%s" % (rel, reports[0].stat().st_size, extra)))
    else:
        r.append((MISS, "批改报告", "缺文件：批改报告/%s/%s-批改报告.md" % (province, paper)))

    # 2) 本次范围，不从规范缓存推断，也不默认最大题号为大作文。
    qs = sorted(set(questions)) if questions is not None else _questions_from_report(_visible_markdown(text))
    if not qs:
        r.append((UNKNOWN, "本次批改范围", "无法确认本次题号，请用 --questions 显式指定；不从旧规范推断"))
    else:
        r.append((OK, "题号", "本次第 %s 题；大作文题号：%s" % (
            "／".join(str(q) for q in qs), "未知" if essay is None else essay or "无")))
    if essay is not None and essay < 0:
        raise ValueError("--essay 必须为非负整数；无大作文填 0")
    if reference_questions is not None and not set(reference_questions).issubset(qs):
        raise ValueError("--reference-questions 只能包含本次 --questions 中的题号")
    if reference_questions is not None and essay in reference_questions:
        raise ValueError("--reference-questions 只填小题／公文，范文用 --exemplars 声明")

    if essay in qs and sample_essay == "available" and exemplars == "none":
        raise ValueError("可照录的本题完整范文须先预评分并纳入可比范文；--sample-essay available 不能与 --exemplars none 同用")

    r.extend(_check_report_content(text, qs, essay, reference_questions, exemplars, sample_essay))

    detailed = len(qs) >= 3
    if qs and not detailed:
        for title in ("小题动态评分规范", "大作文批改规范", "采分点共识表", "大作文刻度文件"):
            r.append((SKIP, title, "本次仅批改 1—2 题，按用户条件免建独立过程文件；证据仍须写入报告"))
    elif detailed:
        if essay is None:
            r.append((UNKNOWN, "题型条件", "请核对本次大作文题号并传 --essay N；无大作文传 --essay 0"))
        else:
            # 3) 本次小题／公文动态规范，无参考答案也应按材料整理。
            for q in qs:
                if q != essay:
                    _check_exists(root, "批改规范/%s/%s-第%d题-动态评分规范.md" % (province, paper, q),
                                  "第%d题动态评分规范" % q, r)
            # 4) 只在本次确有大作文时检查本题规范与范文刻度。
            if essay in qs:
                _check_exists(root, "批改规范/%s/%s-大作文批改规范.md" % (province, paper),
                              "大作文批改规范", r)
                if exemplars == "available":
                    for kind in ("梯队画像", "特征卡", "素材清单"):
                        _check_exists(root, "批改报告/.大作文刻度/%s-%s.md" % (paper, kind),
                                      "刻度·%s" % kind, r)
                elif exemplars == "none":
                    r.append((SKIP, "大作文刻度文件", "已确认无可用完整可比范文，免建；报告须说明已检查范围和原因"))
                else:
                    r.append((UNKNOWN, "范文条件", "请核实可用完整可比范文，传 --exemplars available 或 none"))
            else:
                r.append((SKIP, "大作文过程文件", "本次未批改大作文，免建大作文规范与刻度文件"))

        # 5) 按本次参考答案覆盖要求共识表；有一份参考答案也应记录其覆盖。
        if reference_questions is None:
            r.append((UNKNOWN, "参考答案条件", "请核对本次小题／公文的可用参考答案题号，传 --reference-questions；无则传 none"))
        elif not reference_questions:
            r.append((SKIP, "采分点共识表", "已确认本次小题／公文无参考答案，免建，不虚构来源"))
        else:
            paths = _consensus_paths(root, paper, reference_questions)
            whole = next((p for p in paths if p.name.endswith("-共识表-全卷.md")), None)
            whole_text = whole.read_text(encoding="utf-8") if whole else ""
            for q in reference_questions:
                per_question = root / "批改报告" / ".采分点共识" / ("%s-第%d题-共识表.md" % (paper, q))
                if whole and re.search(r"^#{1,6}\s*第\s*%d\s*题\b" % q, whole_text, re.M):
                    r.append((OK, "第%d题共识表" % q, whole.relative_to(root).as_posix()))
                elif per_question.is_file() and per_question.stat().st_size:
                    r.append((OK, "第%d题共识表" % q, per_question.relative_to(root).as_posix()))
                else:
                    r.append((MISS, "第%d题共识表" % q, "合卷表未覆盖本题，且无本题独立共识表"))
    # 6) 字数核验
    _check_exists(root, "批改报告/.字数核验/%s-字数.md" % paper, "字数核验留痕", r)

    # 8) README 登记行（改卷ID 必登记，见 SKILL.md 9.1/9.2）
    readme = root / "批改报告" / "README.md"
    if not readme.is_file():
        r.append((WARN, "README 登记行", "批改报告/README.md 不存在"))
    elif paper in readme.read_text(encoding="utf-8", errors="replace"):
        r.append((OK, "README 登记行", "已登记：%s" % paper))
    else:
        r.append((WARN, "README 登记行",
                  "未见登记行（卷ID %s 未出现在 批改报告/README.md；用 `grep -n 'INSERT:'` 锚点插入）" % paper))

    # 9) 报告头两块声明（报告质量，非缺件 → ⚠）
    if text:
        for key, note in (("批改上下文声明", "卷级隔离声明"),
                          ("评分依据声明", "评分依据声明")):
            if key in text:
                r.append((OK, note, "已写"))
            else:
                r.append((WARN, note, "报告里未出现「%s」字样" % key))
        if essay not in qs:
            r.append((SKIP, "满分→换算表", "本次未确认包含大作文，不要求小题套用大作文换算表"))
        elif re.search(r"分卷|换算表", text):
            r.append((OK, "满分→换算表", "报告已写明所用分卷换算表"))
        else:
            r.append((WARN, "满分→换算表",
                      "报告未见「分卷／换算表」字样 → 按 scoring-standards.md 三.1，"
                      "须先写「本题满分 N 分 → 查 N 分卷表」"))

    # 10) 家数交叉核对（脚本 vs 共识表声明）
    if run_counts and reference_questions:
        cp, declared = _consensus_counts(root, paper, reference_questions)
        if cp is None:
            if detailed:
                r.append((WARN, "家数交叉核对", "共识表缺失，无法核对"))
        else:
            got = _script_counts(root, province, paper)
            if got and got[0][0] != "error":
                got = [(q, n) for q, n in got if q in reference_questions]
            if got is None:
                r.append((WARN, "家数交叉核对", "参考答案目录或 ref_independence.py 缺失 → 跳过"))
            elif got == [] or (got and got[0][0] == "error"):
                r.append((WARN, "家数交叉核对", "脚本无输出或报错：%s" % (got,)))
            else:
                pairs = [n for _, n in got]
                r.append((OK, "家数（脚本实测）", "第 %s 题：%s 家" % (
                    "／".join(str(q) for q, _ in got), "／".join(str(n) for n in pairs))))
                decl_map = {q: n for q, n in declared}
                both = [(q, n) for q, n in got if q in decl_map]
                diff = [(q, n, decl_map[q]) for q, n in both if n != decl_map[q]]
                only_script = [q for q, _ in got if q not in decl_map]
                if diff:
                    r.append((WARN, "家数交叉核对",
                              "不一致 第 %s 题（脚本／共识表：%s）→ 人工确认，必要时在报告写明「家数为下界」" % (
                                  "／".join(str(q) for q, _, _ in diff),
                                  "；".join("第%d题 %d vs %d" % (q, a, b) for q, a, b in diff))))
                elif both:
                    r.append((OK, "家数交叉核对", "脚本与共识表声明一致（第 %s 题%s）" % (
                        "／".join(str(q) for q, _ in both),
                        "；第 %s 题共识表未写家数（大作文通常只写范文清单）" % "／".join(map(str, only_script))
                        if only_script else "")))
                else:
                    r.append((WARN, "家数交叉核对", "共识表未出现「独立来源 N 家」字样 → 人工确认"))
    return r


def summarize(results):
    miss = [t for lv, t, _ in results if lv == MISS]
    warn = [t for lv, t, _ in results if lv == WARN]
    return miss, warn


# ----------------------------------------------------------------- selftest
def selftest():
    """核对实际文件要求与退出码，覆盖条件豁免、未知和本次范围。"""
    fails = []
    checks = 0

    def eq(got, want, name):
        nonlocal checks
        checks += 1
        if got != want:
            fails.append("%s: got %r want %r" % (name, got, want))

    for raw, plain in (
        ("F1 · `参考答案/卷_甲/第4题.txt`", "F1 · 参考答案/卷_甲/第4题.txt"),
        ("**`F1`** · **来源甲**", "F1 · 来源甲"),
        ("_F1_ · *来源甲*", "F1 · 来源甲"),
        ("__F1__ · ***来源甲***", "F1 · 来源甲"),
        ("`_来源_`", "_来源_"),
        ("`**来源**`", "**来源**"),
        ("``源`文``", "源`文"),
        ("参考答案/卷_甲/范文_乙.txt", "参考答案/卷_甲/范文_乙.txt"),
        ("`F_1`", "F_1"),
    ):
        eq(_cell_value(raw), plain, "行内排版归一化：%s" % raw)

    paper = "2099国考地市级"

    def write(root, rel, body="已核实的过程记录"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def absent(status):
        return "- 状态：%s\n- 已检查：`参考答案/国考/2099国考地市级/`\n- 原因：目录未收录本题可用文本\n" % status

    def fixture(qs, essay=0, refs=(), exemplars="available"):
        parts = ["## 批改上下文声明\n## 评分依据声明\n分卷换算表\n"]
        for q in qs:
            parts.append("### 第%d题（模拟评分）\n" % q)
            if q != essay:
                parts.append("#### 采分点表\n"
                             "|采分点|材料依据|作答原文|命中状态|分值|得分理由|\n"
                             "|---|---|---|---|---|---|\n"
                             "|公共服务下沉|材料1：服务送到村|服务送到村|完整命中|5|覆盖核心动作与对象|\n"
                             "#### 参考答案共识表\n")
                parts.append("|候选要点|材料依据|来源覆盖|重要性与理由|计分状态|分值|\n"
                             "|---|---|---|---|---|---|\n"
                             "|服务下沉|材料1：服务送到村|机构甲|核心任务|计分|5|\n"
                             if q in (refs or ()) else absent("无可用参考答案"))
                parts.append("#### 参考答案\n##### 参考正文\n推动公共服务下沉，将服务送到村民身边。\n")
            elif exemplars == "none":
                for title in ("大作文刻度表", "范文逐篇评分", "论点表"):
                    parts.append("#### %s\n%s" % (title, absent("无可用完整可比范文")))
                parts.append("#### 示例范文\n" + absent("无可照录的本题完整范文"))
            else:
                parts.append("#### 大作文刻度表\n"
                             "|范文ID|来源|参照类型|原题依据|可比与不可比维度|题意|内容与论证|结构|语言|相对位置与局限|\n"
                             "|---|---|---|---|---|---|---|---|---|---|\n"
                             "|F1|机构甲原文|同题|本卷第4题原文及材料，满分40分见题干|可比：本题各维度；不可比：无，同题任务一致|切题|有因果论证|递进完整|流畅|本题上位参照，边界分析不足|\n"
                             "|F2|机构乙原文|同题|本卷第4题原文及材料，满分40分见题干|可比：本题各维度；不可比：无，同题任务一致|切题|分析充分|并列完整|通顺|本题中位参照，机制分析不足|\n"
                             "#### 范文逐篇评分\n"
                             "|范文ID|来源|模拟得分|模拟档位|评分依据（含原文）|\n"
                             "|---|---|---|---|---|\n"
                             "|F1|机构甲原文|32/40|一类下|原文“服务送到村”切题，因果充分，结构完整，语言流畅|\n"
                             "|F2|机构乙原文|30–31/40|二类上|原文“村民少跑路”有事实，结构完整；机制解释的完整性存在歧义|\n"
                             "#### 论点表\n"
                             "|来源|题目|总论点|分论点1|分论点2|分论点3|\n"
                             "|---|---|---|---|---|---|\n"
                             "|F1 · 机构甲原文|服务送到村|公共服务要到达群众身边（第1段）|渠道下沉降低办事成本（第2段）|人员下沉提升服务质量（第3段）|资源下沉促进均等化（第4段）|\n"
                             "|F2 · 机构乙原文|原文无标题|让群众就近享有公共服务（第1段）|增设网点让村民少跑路（第2段）|优化流程缩短办事时间（第3段）|无（本篇仅2个分论点）|\n"
                             "#### 示例范文\n- 范文ID：F1\n- 选取理由：切题且论证充分\n"
                             "> 服务送到村\n> 公共服务需要到达群众身边。通过渠道下沉，减少村民办事的成本。\n")
        return "".join(parts)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        report = "批改报告/国考/%s-批改报告.md" % paper
        write(root, report, "## 批改上下文声明\n## 评分依据声明\n分卷换算表\n"
              "### 第1题（15分）\n### 第2题（20分）\n### 第3题（25分）\n### 第4题（40分）\n")
        write(root, "批改报告/.字数核验/%s-字数.md" % paper)
        write(root, "批改报告/README.md", paper)

        def run(qs, essay=None, refs=None, exemplars="unknown"):
            if qs:
                write(root, report, fixture(qs, essay, refs, exemplars))
            return check_paper(root, "国考", paper, essay=essay, questions=qs,
                               reference_questions=refs, exemplars=exemplars, run_counts=False,
                               sample_essay=exemplars)

        res = run([1, 2])
        eq(summarize(res)[0], [], "只改两题免建过程文件，整卷报告含四题不扩大显式范围")
        eq(any(lv == UNKNOWN for lv, _, _ in res), True, "两题只免独立建档，报告题型与来源条件仍须核实")
        eq(summarize(run([4], essay=4))[0], [], "只改一道大作文免建刻度")
        res = run([1, 2, 3], essay=0, refs=[])
        eq(set(summarize(res)[0]), {"第1题动态评分规范", "第2题动态评分规范", "第3题动态评分规范"},
           "三道小题无参考答案仍建动态规范，免共识及作文文件")
        eq(set(summarize(run([1, 2, 3], essay=4, refs=[]))[0]),
           {"第1题动态评分规范", "第2题动态评分规范", "第3题动态评分规范"},
           "整卷有作文但本次未批改时免作文过程文件")
        res = run([1, 2, 3], essay=3, refs=[1], exemplars="none")
        eq(set(summarize(res)[0]), {"第1题动态评分规范", "第2题动态评分规范", "大作文批改规范", "第1题共识表"},
           "无范文只免刻度，参考答案只覆盖第一题则只要求该题共识表")
        res = run([1, 2, 3, 4], essay=4, refs=[1, 2, 3], exemplars="available")
        eq(set(summarize(res)[0]), {"第1题动态评分规范", "第2题动态评分规范", "第3题动态评分规范",
                                  "大作文批改规范", "第1题共识表", "第2题共识表", "第3题共识表",
                                  "刻度·梯队画像", "刻度·特征卡", "刻度·素材清单"}, "条件全满足时缺件全检出")
        eq(any(lv == UNKNOWN for lv, _, _ in run([1, 2, 3])), True, "未知条件不能冒充无作文／无参考")
        for q in (1, 2, 3):
            write(root, "批改规范/国考/%s-第%d题-动态评分规范.md" % (paper, q))
        write(root, "批改规范/国考/%s-大作文批改规范.md" % paper)
        for kind in ("梯队画像", "特征卡", "素材清单"):
            write(root, "批改报告/.大作文刻度/%s-%s.md" % (paper, kind))
        whole = write(root, "批改报告/.采分点共识/%s-共识表-全卷.md" % paper, "## 第1题\n来源及要点\n")
        res = run([1, 2, 3, 4], essay=4, refs=[1, 2, 3], exemplars="available")
        eq(set(summarize(res)[0]), {"第2题共识表", "第3题共识表"}, "合卷旧表只含第一题不能替代第二三题")
        for q in (2, 3):
            write(root, "批改报告/.采分点共识/%s-第%d题-共识表.md" % (paper, q),
                  "## 第%d题\n独立来源 1 家\n" % q)
        res = run([1, 2, 3, 4], essay=4, refs=[1, 2, 3], exemplars="available")
        eq(summarize(res), ([], []), "合卷表加分题表覆盖本次范围即可齐备")
        _, counts = _consensus_counts(root, paper, [1, 2, 3])
        eq(counts, [(2, 1), (3, 1)], "来源核对能读取实际使用的分题表")
        whole.unlink()
        write(root, "批改报告/.采分点共识/%s-第1题-共识表.md" % paper)
        eq(summarize(run([1, 2, 3], essay=0, refs=[1, 2, 3]))[0], [], "全用分题共识表可齐备")
        write(root, "批改规范/国考/%s-第3题-动态评分规范.md" % paper, "")
        eq(summarize(run([1, 2, 3], essay=0, refs=[]))[0], ["第3题动态评分规范"], "空文件不充当过程记录")
        write(root, "批改规范/国考/%s-第3题-动态评分规范.md" % paper,
              "# 历史稿\n" + SUPERSEDED_MARKER + "\n旧规则及分值表")
        res = run([1, 2, 3], essay=0, refs=[])
        eq(any(level == MISS and title == "第3题动态评分规范" and "已过期" in detail
               for level, title, detail in res), True, "非空旧规则稿不能充当当前必建规范")

        # 缓存齐全也不能替代正文；逐项验证报告内容缺失会阻止交付。
        full = fixture([1, 4], essay=4, refs=[1])
        def content(body, qs=(1, 4), essay=4, refs=(1,), exemplars="available", sample_essay=None):
            sample = exemplars if sample_essay is None else sample_essay
            return _check_report_content(body, qs, essay, refs, exemplars, sample)
        eq(summarize(content(full))[0], [], "两题报告完整展示即可通过正文检查")
        original_reference = ("|F2|机构乙原文|同题|本卷第4题原文及材料，满分40分见题干|"
                              "可比：本题各维度；不可比：无，同题任务一致|切题|分析充分|并列完整|通顺|本题中位参照，机制分析不足|")
        weak_reference = ("|F2|机构乙原文|异题弱参照|原卷B第3题题干及材料，满分50分见原题|"
                          "可比：论证与语言，任务接近且均有因果分析；不可比：题意与内容覆盖，原题要求不同|"
                          "切合原题|原题分析充分|并列完整|通顺|仅作明确可比维度的弱参照|")
        mixed = full.replace(original_reference, weak_reference).replace("30–31/40", "37–38/50")
        eq(summarize(content(mixed))[0], [], "同题40分与异题原题50分的预评可并存")
        for replacement in ("51/50", "38–37/50", "37/0"):
            eq("第4题报告·范文逐篇评分" in summarize(content(mixed.replace("37–38/50", replacement)))[0], True,
               "允许不同满分后仍逐篇拦截越界或无效分数：%s" % replacement)
        for old, new, name in (
            ("|原题依据|", "|原文链接|", "原题依据列缺失"),
            ("|可比与不可比维度|", "|备注|", "可比范围列缺失"),
            ("|异题弱参照|", "|一般参考|", "参照类型未按同题或异题登记"),
            ("|原卷B第3题题干及材料，满分50分见原题|", "|…|", "原题依据为占位"),
        ):
            eq("第4题报告·刻度表" in summarize(content(mixed.replace(old, new)))[0], True, name + "须报错")
        eq("第4题报告·示例范文" in summarize(content(mixed.replace("- 范文ID：F1", "- 范文ID：F2")))[0], True,
           "已评分的异题弱参照不能入选本题示例全文")
        source = "参考答案/国考/2099_甲/第4题.txt"
        plain_source = full.replace("机构甲原文", source)
        for rendered in ("F1 · `" + source + "`", "**F1** · **" + source + "**",
                         "**`F1`** · `" + source + "`", "_F1_ · *" + source + "*"):
            formatted = plain_source.replace("F1 · " + source, rendered)
            eq(summarize(content(formatted))[0], [], "仅论点表使用不同排版仍与纯文本来源对应")
        formatted = plain_source.replace("|F1|" + source + "|", "|**F1**|**" + source + "**|", 1)
        formatted = formatted.replace("|F1|" + source + "|", "|`F1`|`" + source + "`|", 1)
        formatted = formatted.replace("F1 · " + source, "**`F1`** · `" + source + "`")
        formatted = formatted.replace("- 范文ID：F1", "- 范文ID：**`F1`**")
        eq(summarize(content(formatted))[0], [], "三表与示例ID各用不同排版仍正确对应")
        wrong_source = plain_source.replace("F1 · " + source, "**F1** · `" + source.replace("2099_甲", "2099甲") + "`")
        eq("第4题报告·论点表覆盖" in summarize(content(wrong_source))[0], True,
           "真实路径中的下划线差异不能当作排版忽略")
        wrong_id = plain_source.replace("F1 · " + source, "**F_1** · `" + source + "`")
        eq("第4题报告·论点表覆盖" in summarize(content(wrong_id))[0], True,
           "实际ID差异不能被排版归一化掩盖")
        duplicate_id = full.replace("|F2|机构乙原文|", "|**F1**|机构乙原文|")
        eq("第4题报告·范文逐篇评分" in summarize(content(duplicate_id))[0], True,
           "排版不同但实际ID重复仍报错")
        duplicate_argument = full.replace("F2 · 机构乙原文", "**`F1`** · 机构乙原文")
        eq("第4题报告·论点表覆盖" in summarize(content(duplicate_argument))[0], True,
           "论点表排版不同但实际ID重复仍报错")
        eq(summarize(content(fixture([1], refs=[]), [1], 0, [], "unknown"))[0], [],
           "无参考答案保留核实说明，仍交付采分点与参考正文")
        eq(summarize(content(fixture([4], 4, [], "none"), [4], 4, [], "none"))[0], [],
           "无范文四个栏目各有已检查路径及原因即可通过")
        sample_absent = re.sub(r"^#### 示例范文\n.*?(?=^#{1,4} |\Z)",
                               "#### 示例范文\n" + absent("无可照录的本题完整范文"),
                               full, count=1, flags=re.M | re.S)
        eq(summarize(content(sample_absent, sample_essay="none"))[0], [],
           "有可比范文而无可照录全文时，三张表加示例缺失说明可通过")
        eq("第4题报告·示例范文" in summarize(content(sample_absent, sample_essay="available"))[0], True,
           "已确认有示例候选时，缺失说明不能代替应附全文")
        eq(any(level == UNKNOWN and title == "报告示例全文条件"
               for level, title, _ in content(full, sample_essay="unknown")), True,
           "有可比范文不能代推示例全文已核实")
        eq(any(level == UNKNOWN and title == "报告示例全文条件"
               for level, title, _ in content(fixture([4], 4, [], "none"), [4], 4, [], "none", "unknown")), True,
           "无可比范文也须独立核实示例全文条件")
        for field in ("已检查", "原因"):
            incomplete = re.sub(r"^- " + field + r"：.*\n", "", sample_absent, flags=re.M)
            eq("第4题报告·示例范文" in summarize(content(incomplete, sample_essay="none"))[0], True,
               "无可照录全文时缺%s须报错" % field)
        wrong_status = sample_absent.replace("状态：无可照录的本题完整范文", "状态：无可用完整可比范文")
        eq("第4题报告·示例范文" in summarize(content(wrong_status, sample_essay="none"))[0], True,
           "示例全文缺失不能借用可比范文的缺失状态")
        missing_scale = re.sub(r"^#### 大作文刻度表\n.*?(?=^#{1,4} |\Z)", "", sample_absent,
                               count=1, flags=re.M | re.S)
        eq("第4题报告·刻度表" in summarize(content(missing_scale, sample_essay="none"))[0], True,
           "无示例全文不豁免已有可比范文的表格")
        for title in ("采分点表", "参考答案共识表", "参考答案", "大作文刻度表", "范文逐篇评分", "论点表", "示例范文"):
            changed = re.sub(r"^#### " + title + r"\n.*?(?=^#{1,4} |\Z)", "", full, count=1, flags=re.M | re.S)
            eq(bool(summarize(content(changed))[0]), True, "缺本题%s不能通过" % title)
        for old, new, name in (
            ("32/40", "一类下", "只有档位而无数值"),
            ("32/40", "41/40", "得分超过满分"),
            ("32/40", "32/0", "满分为零"),
            ("32/40", "-1/40", "负分"),
            ("30–31/40", "32–30/40", "反向区间"),
            ("30–31/40", "30/50", "同题范文满分不一致"),
            ("|F2|机构乙原文|30–31/40|二类上|原文“村民少跑路”有事实，结构完整；机制解释的完整性存在歧义|\n", "", "漏评一篇"),
            ("|F2|机构乙原文|30–31/40", "|F1|机构乙原文|30–31/40", "范文ID重复"),
            ("|F2|机构乙原文|30–31/40", "|F2|其他来源|30–31/40", "范文来源错配"),
            ("- 范文ID：F1", "- 范文ID：F3", "所选范文未登记"),
            ("推动公共服务下沉，将服务送到村民身边。", "[参考答案](附件.md)", "参考正文只有链接"),
            ("|服务下沉|材料1：服务送到村|机构甲|核心任务|计分|5|", "|…|…|…|…|…|…|", "表格只有占位符"),
        ):
            eq(bool(summarize(content(full.replace(old, new)))[0]), True, name + "不能通过")
        arguments = full[full.index("#### 论点表\n"):full.index("#### 示例范文\n")]
        argument_lines = arguments.splitlines()
        for old, new, name in (
            (argument_lines[1], "|题目|来源|总论点|分论点1|分论点2|分论点3|", "表头顺序错误"),
            (argument_lines[1], "|范文ID|题目|总论点|分论点1|分论点2|分论点3|", "来源列缺失"),
            (argument_lines[4] + "\n", "", "论点表漏一篇"),
            ("F2 · 机构乙原文", "F1 · 机构乙原文", "论点表ID重复"),
            ("F2 · 机构乙原文", "F3 · 机构乙原文", "论点表额外范文"),
            ("F2 · 机构乙原文", "F2 · 其他来源", "论点表来源错配"),
            ("F2 · 机构乙原文", "机构乙原文", "论点表无对应ID"),
            ("|原文无标题|", "||", "标题单元格为空"),
            ("|让群众就近享有公共服务（第1段）|", "|…|", "总论点为占位符"),
            ("|无（本篇仅2个分论点）|", "||", "较少分论点未如实标明"),
        ):
            changed = full.replace(arguments, arguments.replace(old, new))
            eq("第4题报告·论点表" in summarize(content(changed))[0]
               or "第4题报告·论点表覆盖" in summarize(content(changed))[0], True, name + "须报缺项")
        for count in (4, 5):
            expanded_lines = [argument_lines[0],
                              argument_lines[1] + "".join("分论点%d|" % n for n in range(4, count + 1)),
                              argument_lines[2] + "---|" * (count - 3),
                              argument_lines[3] + "".join("完善监督机制（第%d段）|" % (n + 1) for n in range(4, count + 1)),
                              argument_lines[4] + "无（本篇仅2个分论点）|" * (count - 3)]
            expanded = full.replace(arguments, "\n".join(expanded_lines) + "\n")
            eq(summarize(content(expanded))[0], [], "%d个分论点扩列且较少者标明可通过" % count)
            eq("第4题报告·论点表" in summarize(content(expanded.replace("分论点4|", "分论点6|")))[0], True,
               "扩列不连续须报缺项")
            eq("第4题报告·论点表" in summarize(content(expanded.replace("完善监督机制（第5段）|", "|")))[0], True,
               "扩展列为空须报缺项")
        no_arguments = re.sub(r"^#### 论点表\n.*?(?=^#{1,4} |\Z)", "", full, count=1, flags=re.M | re.S)
        other_question = no_arguments.replace("#### 参考答案\n", arguments + "#### 参考答案\n", 1)
        eq("第4题报告·论点表" in summarize(content(other_question))[0], True, "其他题的论点表不能替代本题")
        absent_arguments = fixture([4], 4, [], "none").replace("#### 论点表\n" + absent("无可用完整可比范文"), "")
        eq("第4题报告·论点表" in summarize(content(absent_arguments, [4], 4, [], "none"))[0], True,
           "无范文也须保留论点表栏目及核实说明")
        # 通过实际文件及 check_paper 核验新增栏目，仍适用单题免建过程文件的路径。
        write(root, report, no_arguments.replace("### 第1题", "### 第2题"))
        res = check_paper(root, "国考", paper, questions=[4], essay=4,
                          reference_questions=[], exemplars="available", run_counts=False,
                          sample_essay="available")
        eq("第4题报告·论点表" in summarize(res)[0], True, "单题交付检查检出论点表缺失")
        eq(bool(summarize(content("```markdown\n" + full + "\n```"))[0]), True, "代码块模板不能充当正文")
        eq(bool(summarize(content("<!--\n" + full + "\n-->"))[0]), True, "注释不能充当正文")
        eq(bool(summarize(content(full.replace("### 第4题", "### 第5题")))[0]), True,
           "其他题的表格不能补本题缺项")
        eq(bool(summarize(content(full + "\n### 第1题\n"))[0]), True, "重复题目标题须报错")
        eq(summarize(content(full.replace("推动公共服务下沉，将服务送到村民身边。",
                                         "- 推动公共服务下沉，将服务送到村民身边。")))[0], [],
           "参考正文允许分点列表")
        eq(summarize(content(full.replace("原文“服务送到村”切题", "原文“服务……送到村”切题")))[0], [],
           "有实质内容的引文允许省略号")
        eq(bool(summarize(content(full.replace("推动公共服务下沉，将服务送到村民身边。",
                                              "- 正文字数核验：已完成")))[0]), True,
           "字数元数据不能替代参考正文")
        missing_reason = fixture([4], 4, [], "none").replace("- 原因：目录未收录本题可用文本\n", "")
        eq(bool(summarize(content(missing_reason, [4], 4, [], "none"))[0]), True,
           "无范文但缺核实原因不能通过")
        # 使用当前报告版本做集成检查，独立缓存均存在也仍应失败。
        write(root, report, fixture([1, 2], refs=[1, 2]).replace("#### 参考答案共识表", "#### 外部表链接"))
        res = check_paper(root, "国考", paper, questions=[1, 2], essay=0,
                          reference_questions=[1, 2], run_counts=False)
        eq("第1题报告·共识表" in summarize(res)[0], True, "现有共识缓存不能绕过报告内展示检查")
        # 范围不明时不得由既存规范扩出本次题号。
        write(root, report, "## 批改上下文声明\n## 评分依据声明\n")
        res = run(None)
        eq(any(lv == UNKNOWN and title == "本次批改范围" for lv, title, _ in res), True,
           "报告未明题号且有旧规范时仍提示范围未知")
        selected = "批改报告/国考/%s-v2-批改报告.md" % paper
        write(root, selected, "### 第1题（15分）\n")
        res = check_paper(root, "国考", paper, report=selected, run_counts=False)
        eq(summarize(res)[0], [], "显式选择本次报告，单题不受其他报告影响")
        os.utime(root / report, (2000, 2000))
        os.utime(root / selected, (3000, 3000))
        eq(_report_paths(root, "国考", paper)[0], root / selected, "未指定报告时按修改时间选择最新版本")
        res = check_paper(root, "国考", paper, report=report, run_counts=False)
        eq(any(level == UNKNOWN for level, _, _ in res), True, "显式报告优先于修改时间较新的其他报告")
        standard_v2 = "批改报告/国考/%s-批改报告-v2.md" % paper
        write(root, standard_v2, fixture([1], refs=[]))
        os.utime(root / standard_v2, (4000, 4000))
        res = check_paper(root, "国考", paper, questions=[1], essay=0,
                          reference_questions=[], run_counts=False)
        eq(_report_paths(root, "国考", paper)[0], root / standard_v2,
           "规范命名的 v2 比旧式 v2 更新时自动选中规范版本")
        eq(summarize(res)[0], [], "自动选中的规范 v2 正文完整时通过")
        standard_v3 = "批改报告/国考/%s-批改报告-v3.md" % paper
        write(root, standard_v3, fixture([1], refs=[]).replace("#### 参考答案\n", "#### 外部链接\n"))
        os.utime(root / standard_v3, (5000, 5000))
        unrelated = "批改报告/国考/%s扩展-批改报告-v4.md" % paper
        write(root, unrelated, fixture([1], refs=[]))
        os.utime(root / unrelated, (6000, 6000))
        matching_directory = root / "批改报告" / "国考" / (paper + "-批改报告-v5.md")
        matching_directory.mkdir()
        eq(set(_report_paths(root, "国考", paper)),
           {root / report, root / selected, root / standard_v2, root / standard_v3},
           "只发现同卷的基础、规范与旧式版本，排除相似卷ID和同名目录")
        res = check_paper(root, "国考", paper, questions=[1], essay=0,
                          reference_questions=[], run_counts=False)
        eq(_report_paths(root, "国考", paper)[0], root / standard_v3,
           "规范 v3 最新时自动选中，不受其他卷较新文件影响")
        eq("第1题报告·参考答案" in summarize(res)[0], True,
           "最新规范 v3 缺正文时失败，不借完整 v2 蒙混通过")
        res = check_paper(root, "国考", paper, report=standard_v2, questions=[1], essay=0,
                          reference_questions=[], run_counts=False)
        eq(summarize(res)[0], [], "显式指定完整 v2 优先于较新的缺项 v3")
        import contextlib
        import io
        def cli(args):
            with contextlib.redirect_stdout(io.StringIO()):
                return main(["--root", str(root), "--paper", paper, "--no-counts"] + args)
        write(root, report, fixture([1, 2]))
        eq(cli(["--questions", "1,2", "--essay", "0", "--reference-questions", "none", "--report", report]), 0,
           "CLI 少量题目完整报告且条件已核实退出零")
        write(root, report, fixture([1, 2]).replace("#### 采分点表", "#### 采分点链接"))
        eq(cli(["--questions", "1,2", "--essay", "0", "--reference-questions", "none", "--report", report]), 1,
           "CLI 报告内容缺项退出一，即使少量题目")
        eq(cli(["--questions", "1,2,3", "--essay", "0", "--reference-questions", "none"]), 1,
           "CLI 必建缺件退出一")
        eq(cli(["--questions", "1,2,3"]), 2, "CLI 条件未知退出二，不误报齐备")
        essay_args = ["--questions", "4", "--essay", "4", "--reference-questions", "none", "--report", report]
        write(root, report, fixture([4], 4, []))
        eq(cli(essay_args + ["--exemplars", "available"]), 2, "CLI 示例全文条件未独立核实退出二")
        eq(cli(essay_args + ["--exemplars", "available", "--sample-essay", "available"]), 0,
           "CLI 有参照且有示例全文退出零")
        write(root, report, sample_absent)
        eq(cli(essay_args + ["--exemplars", "available", "--sample-essay", "none"]), 0,
           "CLI 有参照而无示例全文的缺失说明通过")
        eq(cli(essay_args + ["--exemplars", "available", "--sample-essay", "available"]), 1,
           "CLI 有示例候选却缺全文退出一")
        write(root, report, fixture([4], 4, [], "none"))
        eq(cli(essay_args + ["--exemplars", "none", "--sample-essay", "none"]), 0,
           "CLI 两类范文均缺失且分别有说明退出零")
        write(root, report, mixed)
        eq(cli(essay_args + ["--exemplars", "available", "--sample-essay", "available"]), 0,
           "CLI 原题满分不同且各自有依据的范文报告通过")
        write(root, report, mixed.replace("37–38/50", "51/50"))
        eq(cli(essay_args + ["--exemplars", "available", "--sample-essay", "available"]), 1,
           "CLI 原题满分不同但其中一篇越界仍退出一")
        try:
            check_paper(root, "国考", paper, questions=[4], essay=4, reference_questions=[],
                        exemplars="none", sample_essay="available", run_counts=False, report=report)
        except ValueError:
            inconsistent = True
        else:
            inconsistent = False
        eq(inconsistent, True, "已确认本题示例候选却声明无任何可比范文须提示条件冲突")

    print("自检 %d 项" % checks)
    if fails:
        for f in fails:
            print("FAILED %s" % f)
        print("selftest: FAILED")
        return 1
    print("selftest: all passed")
    return 0


# ----------------------------------------------------------------- CLI
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(
        description="卷级一致性检查：核一卷的批改产物是否齐备、登记是否到位（只读）")
    ap.add_argument("--paper", help="卷ID，如 2019国考地市级")
    ap.add_argument("--province", default="国考", help="省份目录名（默认 国考）")
    ap.add_argument("--questions", type=_question_list, default=None, help="本次实际批改题号，如 1,2,3；默认仅从本次报告题标题读取")
    ap.add_argument("--essay", type=int, default=None, help="已核实的大作文题号；无大作文填 0，未提供则未知，不猜最后一题")
    ap.add_argument("--reference-questions", type=_question_list, default=None, help="本次有可用参考答案的小题／公文题号，如 1,2；无则填 none")
    ap.add_argument("--exemplars", choices=("available", "none", "unknown"), default="unknown", help="本次大作文是否有可用完整可比范文，须实际核对")
    ap.add_argument("--sample-essay", choices=("available", "none", "unknown"), default="unknown", help="本卷参考答案中是否有可照录的同题完整范文，与比较参照分别核实")
    ap.add_argument("--report", help="本次报告路径（相对工作区或绝对路径）；默认按修改时间选择最新报告")
    ap.add_argument("--root", default=None, help="工作区根目录（默认脚本上一级）")
    ap.add_argument("--no-counts", action="store_true", help="跳过家数交叉核对（不跑 ref_independence）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--quiet", action="store_true", help="只输出缺失与告警")
    ap.add_argument("--selftest", action="store_true", help="内置自检（忽略其他参数）")
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not a.paper:
        ap.error("--paper 必填（或使用 --selftest）")

    root = Path(a.root).resolve() if a.root else Path(__file__).resolve().parent.parent
    if not (root / "批改报告").is_dir():
        print("✗ 找不到 %s/批改报告 → 用 --root 指定工作区根目录" % root, file=sys.stderr)
        return 2

    try:
        res = check_paper(root, a.province, a.paper, essay=a.essay, run_counts=not a.no_counts,
                          questions=a.questions, reference_questions=a.reference_questions,
                          exemplars=a.exemplars, report=a.report, sample_essay=a.sample_essay)
    except ValueError as exc:
        ap.error(str(exc))
    miss, warn = summarize(res)
    unknown = [title for level, title, _ in res if level == UNKNOWN]
    exit_code = 2 if unknown else 1 if miss else 0

    if a.json:
        print(json.dumps({"paper": a.paper, "province": a.province,
                          "results": [{"level": lv, "title": t, "detail": d} for lv, t, d in res],
                          "missing": miss, "warnings": warn,
                          "unverified": unknown, "ok": not miss and not unknown}, ensure_ascii=False, indent=2))
        return exit_code

    print("卷级一致性检查 · %s（%s）" % (a.paper, a.province))
    print("-" * 60)
    for lv, title, detail in res:
        if a.quiet and lv in (OK, SKIP):
            continue
        print("%s %s：%s" % (MARK[lv], title, detail))
    print("-" * 60)
    if miss:
        print("✗ 缺件 %d 项：%s" % (len(miss), "；".join(miss)))
    if warn:
        print("⚠ 提示 %d 项：%s" % (len(warn), "；".join(warn)))
    if unknown:
        print("? 条件未核实：%s；本次不能宣称齐备" % "；".join(unknown))
    if not miss and not warn and not unknown:
        print("✓ 全部齐备")
    elif not miss and not unknown:
        print("✓ 无缺件（有提示，按需处理）")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
