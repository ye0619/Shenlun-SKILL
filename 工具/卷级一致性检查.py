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
    python 工具/卷级一致性检查.py --paper 2019国考地市级
    python 工具/卷级一致性检查.py --paper 2023浙江C --province 浙江
    python 工具/卷级一致性检查.py --paper 2019国考地市级 --json
    python 工具/卷级一致性检查.py --selftest

约定（与 workspace-layout.md / report-template.md 一致）
-------------------------------------------------------
    报告         批改报告/{省份}/{卷ID}-批改报告.md（可另有 {卷ID}-v2-批改报告.md）
    小题规范     批改规范/{省份}/{卷ID}-第N题-动态评分规范.md
    大作文规范   批改规范/{省份}/{卷ID}-大作文批改规范.md
    共识表       批改报告/.采分点共识/{卷ID}-共识表-全卷.md
    字数核验     批改报告/.字数核验/{卷ID}-字数.md
    刻度三件套   批改报告/.大作文刻度/{卷ID}-{梯队画像,特征卡,素材清单}.md
    登记行       批改报告/README.md 内出现该卷ID

退出码：0＝齐备（或仅 ⚠ 提示）；1＝有 ✗ 缺件；2＝参数或环境错误。
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

VERSION = "1.0.0（2026-10-11）"

OK, WARN, MISS = "ok", "warn", "miss"
MARK = {OK: "✓", WARN: "⚠", MISS: "✗"}


# ----------------------------------------------------------------- 单项检查
def _check_exists(root: Path, rel: str, title: str, results: list, level=MISS):
    p = root / rel
    if p.is_file():
        results.append((OK, title, "%s（%d 字节）" % (rel, p.stat().st_size)))
        return True
    results.append((level, title, "缺文件：%s" % rel))
    return False


def _report_paths(root: Path, province: str, paper: str):
    d = root / "批改报告" / province
    if not d.is_dir():
        return []
    return sorted(d.glob("%s-*批改报告.md" % paper))


def _questions_from_report(text: str):
    """从报告正文里取题号（`### 第N题（…）`）。"""
    return sorted({int(m) for m in re.findall(r"^#{2,4}\s*第\s*(\d+)\s*题", text, re.M)})


def _questions_from_specs(root: Path, province: str, paper: str):
    d = root / "批改规范" / province
    if not d.is_dir():
        return []
    pat = re.compile(re.escape(paper) + r"-第\s*(\d+)\s*题")
    out = set()
    for p in d.iterdir():
        m = pat.match(p.name)
        if m:
            out.add(int(m.group(1)))
    return sorted(out)


def _consensus_counts(root: Path, paper: str):
    """共识表里每题声明的"独立来源 N 家"数字。

    按 `## 第N题` 切段，**每段只取第一处"独立来源 N 家"**——共识表 §2 之后往往还会
    引用脚本实测值或修复前后的对照（如"修复后独立来源 16 家"），取第一处才等于
    该题的人工清点结论（2026-10-11 修正：此前取全文所有匹配，会把对照值也当成声明值）。
    """
    p = root / "批改报告" / ".采分点共识" / ("%s-共识表-全卷.md" % paper)
    if not p.is_file():
        return None, []
    text = p.read_text(encoding="utf-8")
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
    return p, pairs


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
def check_paper(root: Path, province: str, paper: str, essay=None, run_counts=True):
    r = []
    # 1) 报告（允许 v2 变体）
    reports = _report_paths(root, province, paper)
    text = ""
    if reports:
        rel = reports[0].relative_to(root).as_posix()
        text = reports[0].read_text(encoding="utf-8", errors="replace")
        extra = "；另有 %s" % "、".join(
            p.name for p in reports[1:]) if len(reports) > 1 else ""
        r.append((OK, "批改报告", "%s（%d 字节）%s" % (rel, reports[0].stat().st_size, extra)))
    else:
        r.append((MISS, "批改报告", "缺文件：批改报告/%s/%s-批改报告.md" % (province, paper)))

    # 2) 题号集合
    qs = _questions_from_report(text) or _questions_from_specs(root, province, paper)
    if essay is None:
        essay = max(qs) if qs else None
    if not qs:
        r.append((WARN, "题号", "无法从报告/规范推断题号 → 跳过「小题规范」逐题核验"))
    else:
        r.append((OK, "题号", "第 %s 题（大作文＝第 %s 题）" % (
            "／".join(str(q) for q in qs), essay)))

    # 3) 小题规范（大作文题除外）
    if qs:
        miss = []
        for q in qs:
            if q == essay:
                continue
            rel = "批改规范/%s/%s-第%d题-动态评分规范.md" % (province, paper, q)
            if not (root / rel).is_file():
                miss.append(q)
        if miss:
            r.append((MISS, "小题动态评分规范", "缺第 %s 题（应落盘 批改规范/%s/%s-第N题-动态评分规范.md）" % (
                "／".join(str(q) for q in miss), province, paper)))
        else:
            r.append((OK, "小题动态评分规范", "第 %s 题齐备" % "／".join(
                str(q) for q in qs if q != essay)))
        # 4) 大作文规范
        _check_exists(root, "批改规范/%s/%s-大作文批改规范.md" % (province, paper),
                      "大作文批改规范", r)

    # 5) 共识表（大作文卷允许只写"范文清单与刻度结论"）
    _check_exists(root, "批改报告/.采分点共识/%s-共识表-全卷.md" % paper, "采分点共识表", r)
    # 6) 字数核验
    _check_exists(root, "批改报告/.字数核验/%s-字数.md" % paper, "字数核验留痕", r)
    # 7) 刻度三件套
    for kind in ("梯队画像", "特征卡", "素材清单"):
        _check_exists(root, "批改报告/.大作文刻度/%s-%s.md" % (paper, kind), "刻度·%s" % kind, r)

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
        if re.search(r"分卷|换算表", text):
            r.append((OK, "满分→换算表", "报告已写明所用分卷换算表"))
        else:
            r.append((WARN, "满分→换算表",
                      "报告未见「分卷／换算表」字样 → 按 scoring-standards.md 三.1，"
                      "须先写「本题满分 N 分 → 查 N 分卷表」"))

    # 10) 家数交叉核对（脚本 vs 共识表声明）
    if run_counts and qs:
        cp, declared = _consensus_counts(root, paper)
        if cp is None:
            r.append((WARN, "家数交叉核对", "共识表缺失，无法核对"))
        else:
            got = _script_counts(root, province, paper)
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
    fails = []

    def eq(got, want, name):
        if got != want:
            fails.append("%s: got %r want %r" % (name, got, want))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, body in (
            ("批改报告/国考/2099国考地市级-批改报告.md",
             "# 报告\n## 一、批改上下文声明\n## 二、评分依据声明\n本题满分 15 分 → 查 15 分卷\n"
             "### 第1题（15分）：x\n### 第2题（20分）：y\n"),
            ("批改规范/国考/2099国考地市级-第1题-动态评分规范.md", "spec"),
        ):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        res = check_paper(root, "国考", "2099国考地市级", essay=2, run_counts=False)
        miss, warn = summarize(res)
        eq(miss, ["大作文批改规范", "采分点共识表", "字数核验留痕",
                  "刻度·梯队画像", "刻度·特征卡", "刻度·素材清单"], "缺件清单")
        eq("README 登记行" in warn, True, "README 未登记告警")
        eq(any("评分依据声明" == t and lv == OK for lv, t, _ in res), True, "评分依据声明已写")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel in ("批改报告/国考/2099国考地市级-批改报告.md",
                    "批改规范/国考/2099国考地市级-第1题-动态评分规范.md",
                    "批改规范/国考/2099国考地市级-大作文批改规范.md",
                    "批改报告/.采分点共识/2099国考地市级-共识表-全卷.md",
                    "批改报告/.字数核验/2099国考地市级-字数.md",
                    "批改报告/.大作文刻度/2099国考地市级-梯队画像.md",
                    "批改报告/.大作文刻度/2099国考地市级-特征卡.md",
                    "批改报告/.大作文刻度/2099国考地市级-素材清单.md"):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("### 第1题（15分）\n## 一、批改上下文声明\n## 二、评分依据声明\n分卷表\n",
                         encoding="utf-8")
        (root / "批改报告" / "README.md").parent.mkdir(parents=True, exist_ok=True)
        (root / "批改报告" / "README.md").write_text(
            "| 2099国考地市级 | 80 |\n", encoding="utf-8")
        res = check_paper(root, "国考", "2099国考地市级", essay=1, run_counts=False)
        miss, warn = summarize(res)
        eq(miss, [], "齐备卷无缺件")
        eq(warn, [], "齐备卷无告警")

    print("自检 %d 项" % 2)
    if fails:
        for f in fails:
            print("FAILED %s" % f)
        print("selftest: FAILED")
        return 1
    print("selftest: all passed")
    return 0


# ----------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="卷级一致性检查：核一卷的批改产物是否齐备、登记是否到位（只读）")
    ap.add_argument("--paper", help="卷ID，如 2019国考地市级")
    ap.add_argument("--province", default="国考", help="省份目录名（默认 国考）")
    ap.add_argument("--essay", type=int, default=None, help="大作文题号（默认取最大题号）")
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

    res = check_paper(root, a.province, a.paper, essay=a.essay, run_counts=not a.no_counts)
    miss, warn = summarize(res)

    if a.json:
        print(json.dumps({"paper": a.paper, "province": a.province,
                          "results": [{"level": lv, "title": t, "detail": d} for lv, t, d in res],
                          "missing": miss, "warnings": warn,
                          "ok": not miss}, ensure_ascii=False, indent=2))
        return 1 if miss else 0

    print("卷级一致性检查 · %s（%s）" % (a.paper, a.province))
    print("-" * 60)
    for lv, title, detail in res:
        if a.quiet and lv == OK:
            continue
        print("%s %s：%s" % (MARK[lv], title, detail))
    print("-" * 60)
    if miss:
        print("✗ 缺件 %d 项：%s" % (len(miss), "；".join(miss)))
    if warn:
        print("⚠ 提示 %d 项：%s" % (len(warn), "；".join(warn)))
    if not miss and not warn:
        print("✓ 全部齐备")
    elif not miss:
        print("✓ 无缺件（有提示，按需处理）")
    return 1 if miss else 0


if __name__ == "__main__":
    sys.exit(main())
