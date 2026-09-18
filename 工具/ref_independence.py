#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参考答案同源检测器（shenlun-judge「多源共识模型」的独立来源家数判定工具）

用途：对同一题的多个参考答案（机构解析／名师口径）两两计算文本相似度并分组，
      输出「独立来源家数 = 分组数」。因为机构之间大量互相转抄（同一机构的不同产品线、
      网上转抄的"机构解析"），**文件数 ≠ 独立来源家数**——本脚本用客观的文本重合率
      代替模型拍脑袋判断，使「共识点 = ≥2 家独立来源命中」可复核、可留痕。

⚠ 边界（重要）：本脚本**只用于参考答案彼此之间判同源**，
   **不参与给分、不影响考生得分**。它是本 skill「评分不用相似度」这条铁律的
   唯一例外：相似度只用来回答"这几份答案算几家"，不用来比较"考生作答与参考答案像不像"
   （考生作答一律按语义等价给分，见 small-questions.md 铁律 8）。

📌 家数口径（用户 2026-09 决定，务必知悉）：
   1. **同一机构的不同答案，视为两份不同答案**（各计 1 家）——本脚本**不按机构名合并**；
      只有**文本高度重合**（ratio ≥ --near，默认 0.60）才判为同源转抄、并成一组。
      输出里会提示"同一机构出现在多个组"，但**只提示、不合并**。
   2. **0 字空条目不计入独立来源家数**（空文件与"是否命中某个采分点"无关，
      若计入会让"1 家真答案 + 1 个空占位文件"跨过"≥2 家独立"的门槛）。
   3. **切分口径的两条硬规则（2026-09 修订，修正两处实测误判）**：
      a. **H1（`# …`）不认白名单外的名字**。参考答案文件里 H1 通常是**范文/公文标题**
         （如 `# 做隐形冠军`），5 个字、无空格、无数字，形态上和"名师名"无法区分；
         一旦误认，就会把整篇范文切成两个条目、并让紧随其后的机构名变成"0 字空条目"。
         机构标签的惯例是 H2–H6（`## 粉笔`／`### 站长`）。
         **实测**：2018 浙江 A 第 3 题原报"3 家 + 1 空条目"，修正后为 **5 家**。
      b. **剥掉"另一份答案"式的尾部括号限定**：`中公（参考答案 1）`＝`中公`、
         `华图【答案二】`＝`华图`；但 `粉笔（某笔）` 这类**别名不剥**（那是另一条产品线）。
         **实测**：同上题，两条中公答案原被并成 1 条，修正后各计 1 家。
      ⚠ 若你的答案文件用 `# 粉笔` 这种 H1 写机构名，请改成 `## 粉笔`（或加白名单/用 `--no-split`）。

用法：
  python ref_independence.py 参考答案/河南/2022河南乡镇            # 目录（递归找答案文件）
  python ref_independence.py 参考答案/河南/2023河南市级 --question 2
  python ref_independence.py 第1题.txt 第1题-粉笔.txt --near 0.65 --same 0.85
  python ref_independence.py 参考答案/河南/2024河南县级 --json > 独立来源.json
  python ref_independence.py --selftest                            # 内置自检
  python ref_independence.py 参考答案/国考/2026国考行政执法 --quiet --sensitivity

参数：
  路径            目录（递归找 .txt/.md 参考答案）或若干具体文件。可给多个。
  --question N    只分析第 N 题（N 支持阿拉伯数字，也接受"一~二十"）。
                  不给则按题号分组建表，全部输出。
                  ⚠ 题号识别不出（None）的条目会被本参数过滤掉，**家数会少算**——
                  这类条目会被记录并告警（JSON 字段 `filtered_no_question`），
                  不会静默丢弃。
  --same FLOAT    同源转抄阈值，默认 0.80（ratio ≥ --same → 判为同源转抄）。
  --near FLOAT    "高度重合/疑似同源"阈值，默认 0.60。保守策略：区间 [near, same)
                  也并入同一组，避免把"转抄后改写"的答案当成两家独立来源。
  --min-chars N   归一化后短于 N 字的条目标记"疑为节选（口径可能不完整）"，默认 100。
  --json          输出机器可读 JSON（便于写进报告留痕）。
  --quiet         只输出汇总，不逐对列出相似度明细。
  --selftest      内置自检（临时目录造样本，不写工作区）；给了路径也忽略路径。
  --no-split      关闭"文件内按机构/名师标签切分"。默认开启：因为工作区
                  参考答案/{省份}/{卷ID}/第N题.txt 往往是**一个文件里并列多家口径**
                  （`### 粉笔`、`**粉笔：**`、`某笔：……`、单独一行的 `站长` 等），
                  不切分会把"一家"误算成"一家文件"从而严重低估来源数。
                  切分后每个"来源条目"= 一个机构/名师的一段口径。
  --sensitivity   额外打印阈值敏感性表（0.50/0.60/0.70/0.80/0.90 各阈值下的独立来源家数），
                  用于判断当前阈值是否合理。
  --no-autojunk   关闭 difflib 的 autojunk 启发式（默认按标准库默认值开启）。长文本
                  上 autojunk 会把高频汉字当"垃圾元素"，可用来交叉验证结论稳不稳。
  -h/--help       显示本帮助。

相似度口径：
  1) 按 UTF-8 读（失败回退 gbk，再失败 latin-1 并提示）；去 BOM；
  2) 删除所有空白字符（空格/制表/换行/全角空格/零宽字符），**保留标点**；
  3) 若同一题下多数条目以"参考答案：""【参考答案】"等统一前缀开头，则去掉该前缀行；
  4) 用 difflib.SequenceMatcher(None, a, b).ratio() 计算两两相似度；
  5) 并查集分组：ratio ≥ near 即并入同组（[near, same) 记为"疑似同源"，仍算同组）。
  空文件（0 字）不参与相似度判定，单列并提示。
  字数为"去空白字符数"（含标点，与 count_chars.py 的答题卡口径同源）。

输出：
  默认人类可读（按题分组 → 组内成员 → 相似度明细 → 节选提示 → 总汇总表）；
  --json 输出 {"questions":[...],"summary":{...}}。

退出码：0 = 正常；2 = 参数错误；3 = 路径不存在。

依据：分组结果属**经验校准（L4）**，只用于判定"几家独立来源"，
     不代表官方评分细则，也不改变任何得分。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile

VERSION = "1.0.0"
DEFAULT_SAME = 0.80
DEFAULT_NEAR = 0.60
DEFAULT_MIN_CHARS = 100

# ---------------------------------------------------------------- 输出编码
def _setup_streams():
    """让中文/符号在管道（非 tty）下按 UTF-8 输出，避免 Windows 默认 gbk 报错或乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(stream, "encoding", "") or "").lower()
        except Exception:
            continue
        try:
            if "utf" in enc:
                stream.reconfigure(errors="replace")
            elif not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")   # 真控制台：保持本机编码但别崩
        except Exception:
            pass


# ---------------------------------------------------------------- 机构/名师白名单
# 与 批改报告/.字数核验/scan_ref_sources.py 的标签口径保持一致（含脱敏写法）
WHITELIST = {
    "粉笔": "粉笔", "某笔": "粉笔（某笔）", "粉笔蒋春旭": "粉笔蒋春旭", "粉笔教育": "粉笔",
    "华图": "华图", "某图": "华图（某图）", "华图教育": "华图",
    "中公": "中公", "某公": "中公（某公）", "中公教育": "中公",
    "站长": "站长", "半月谈": "半月谈", "半月谈白鹭": "半月谈白鹭",
    "白鹭": "白鹭", "花木君": "花木君", "申研社": "申研社", "贺冲": "贺冲", "小飞仔": "小飞仔",
    "人须在事上磨": "人须在事上磨", "程诺": "程诺", "单淑玲": "单淑玲", "张嘉庆": "张嘉庆",
    "袁东": "袁东", "袁东老师": "袁东", "飞扬": "飞扬", "飞扬老师": "飞扬",
    "四海飞扬": "飞扬", "四海飞扬老师": "飞扬", "四海": "四海",
    "小马哥": "小马哥", "公考小马哥": "小马哥", "小张": "小张", "金标尺": "金标尺",
    "中指": "中指", "江牧云": "江牧云", "新途径": "新途径", "新职途": "新职途",
    "岭南贡院": "岭南贡院", "京佳教育": "京佳教育", "逸学公考": "逸学公考",
    "超格李崇立": "超格李崇立", "超格冰哥": "超格冰哥", "朱老师": "朱老师",
    "22 老师": "22 老师", "22老师": "22 老师", "高分学员": "高分学员",
    "公考沈建春老师": "公考沈建春老师", "小红薯公考静姐老师": "小红薯公考静姐老师",
    "一眉巫师": "一眉巫师", "相丽君": "相丽君", "上岸村": "上岸村", "公道公考": "公道公考",
    "申良言": "申良言", "导氮教育": "导氮教育", "导氮": "导氮教育",
    "Ace 超凡申论": "Ace 超凡申论", "Ace超凡申论": "Ace 超凡申论",
    "淘宝试卷解析": "淘宝试卷解析", "公考某老师": "公考某老师",
    "远志公考思维课堂": "远志公考思维课堂", "高资 up": "高资 up", "高资up": "高资 up",
    "张安威": "张安威", "师心不自用": "师心不自用", "申言说": "申言说",
    "唐棣讲申论": "唐棣讲申论", "青云端公考徐阳老师": "青云端公考徐阳老师",
    "高分网友回忆版": "高分网友回忆版", "刘司柠": "刘司柠", "王鹍": "王鹍",
    "千寻": "千寻", "独立申论": "独立申论", "OK 公考": "OK 公考", "OK公考": "OK 公考",
    "申论大湿": "申论大湿", "半月谈 —— 老师": "半月谈",
}
# 按长度倒序，保证"半月谈白鹭"先于"半月谈"命中
_WL_KEYS = sorted((k for k, v in WHITELIST.items() if v), key=len, reverse=True)

DENY_NAME_RE = re.compile(
    r"^(?:第\s*[0-9一二三四五六七八九十百]{1,4}\s*[题大]"
    r"|题目|题[干目]|要求|作答要求|参考答案|参考解析|答案|解析|材料|给定资料|评分"
    r"|说明|注|示例|范文|批注|备注|审题|思路|解答|分析|作答"
    r"|一、|二、|三、|四、|五、|六、|七、|八、|九、|十、)")
# 纯"名字样"：CJK/字母开头，允许内部空格·-—_，不允许数字与句读
NAME_OK_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z ·\-—_]{1,23}$")
HEADING_RE = re.compile(r"^(#{1,6})\s*(.*)$")
BOLD_RE = re.compile(r"^\*\*(.+?)\*\*\s*[：:]?\s*(.*)$")
COLON_NAME_RE = re.compile(r"^([^：:]{2,26})[：:]\s*(.*)$")
PLAIN_NUM_RE = re.compile(r"^\s*(?:[0-9]{1,3}|[（(][0-9一二三四五六七八九十]{1,3}[)）])[、.．)）]")
Q_HEAD_RE = re.compile(r"^#{0,6}\s*第\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*(?:小题|大题|题)")
WS_RE = re.compile(r"[\s\u200b\ufeff]+")
PREFIX_RES = [
    re.compile(r"^#{0,6}\s*参考答案\s*[：:]?\s*"),
    re.compile(r"^#{0,6}\s*【参考答案】\s*[：:]?\s*"),
    re.compile(r"^#{0,6}\s*参考解答\s*[：:]?\s*"),
]

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(s):
    """中文数字（一~二十，以及 二十一…九十九）→ int；失败返回 None。"""
    if s is None:
        return None
    s = s.strip()
    if s.isdigit():
        return int(s)
    if not s or any(c not in CN_DIGITS and c != "十" for c in s):
        return None
    if "十" in s:
        left, _, right = s.partition("十")
        tens = CN_DIGITS.get(left, 1) if left else 1
        ones = CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for ch in s:
        total = total * 10 + CN_DIGITS[ch]
    return total


def extract_question(name):
    """从文件名里识别题号；识别不到返回 None。"""
    m = Q_HEAD_RE.search(name)
    if m:
        return cn_to_int(m.group(1))
    m = re.search(r"第\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*题", name)
    if m:
        return cn_to_int(m.group(1))
    m = re.search(r"题目\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})", name)
    if m:
        return cn_to_int(m.group(1))
    return None


NAME_SUFFIXES = ("老师", "教育", "公考", "申论", "讲申论", "大湿", "机构", "团队", "老师讲申论")


def _norm_key(s):
    """标签比较用的归一化：去掉空白与连接符（“超格 — 冰哥”＝“超格冰哥”、“高资 up”＝“高资up”）。"""
    return re.sub(r"[\s\u3000·\-—_/＋+．.]", "", s or "")


_WL_NORM = {}
for _k, _v in WHITELIST.items():
    if _v:
        _WL_NORM.setdefault(_norm_key(_k), _v)
_WL_NORM_KEYS = sorted(_WL_NORM, key=len, reverse=True)
_NORM_SUFFIXES = tuple(_norm_key(s) for s in NAME_SUFFIXES)


_BRACKET_AUX_RE = re.compile(
    r"[（(【\[〔]\s*(?:参考\s*)?(?:答案|解析|题解|版本|范文|示例)"
    r"\s*[0-9０-９一二三四五六七八九十]*\s*(?:套|版|篇|则|稿|种)?\s*[)）】\]〕]\s*$"
)

# 整体包裹的装饰括号（`【粉笔】`／`（华图）`／`「站长」`）——只在两端成对时剥掉
_WRAPPED_RE = re.compile(r"^[【\[〔（(《<「『]\s*(.+?)\s*[】\]〕）)》>」』]$")


def canon_label(raw):
    """把候选标签规范化成机构/名师名；命中白名单才返回非 None。"""
    if not raw:
        return None
    c = raw.strip().strip("：: 　").strip("*`# ").strip()
    if not c or DENY_NAME_RE.match(c):
        return None
    # 去掉"另一份答案"式的尾部括号限定：`中公（参考答案 1）`＝`中公`、`华图【答案二】`＝`华图`。
    # 只剥掉含"参考答案/答案/解析/版本…"的括号，`粉笔（某笔）` 这类别名**不剥**（那是另一条产品线）。
    c2 = _BRACKET_AUX_RE.sub("", c).strip()
    if c2:
        c = c2
    # 去掉整体包裹的装饰括号：`【粉笔】`／`（华图）`／`「站长」`＝`粉笔`／`华图`／`站长`。
    # 只剥"整串被一对括号包住"的情形，不动 `中公（参考答案 1` 这种只在一侧的残尾。
    for _ in range(2):
        m = _WRAPPED_RE.match(c)
        if not m:
            break
        c = m.group(1).strip()
        if not c:
            return None
    if c in WHITELIST:
        return WHITELIST[c]
    nc = _norm_key(c)
    if not nc:
        return None
    if nc in _WL_NORM:
        return _WL_NORM[nc]
    # 白名单名 + 短后缀（如"袁东老师""华图教育""超格—冰哥"）；后缀过长则不认
    for k in _WL_NORM_KEYS:
        if len(k) >= 2 and nc.startswith(k):
            tail = nc[len(k):]
            if tail in _NORM_SUFFIXES or (0 < len(tail) <= 2 and all(
                    "\u4e00" <= ch <= "\u9fa5" for ch in tail)):
                return _WL_NORM[k]
    for k in _WL_KEYS:
        if c == k:
            return WHITELIST[k]
    return None


def _unknown_name_ok(name, hashes=2, wrapped=False):
    """白名单未命中时，是否仍把它当作来源标签。

    只认"短名字样"（≤8 字、无空格、无数字）——否则会把范文/公文标题当成机构名
    （如 `### 锚定执法目标 绘就数字时代执法新画卷`、`# 关于推荐 B 市…材料`）。

    `hashes` 为该行的 `#` 个数，`wrapped` 表示名字被一对装饰括号整体包住（`【先声夺人】`）。
    **H1（`# …`）只认"括号包裹"的未知名字**：参考答案文件里裸 H1 通常是**范文/公文标题**
    （如 `# 做隐形冠军`），5 字、无空格、无数字，形态上和"机构/名师名"无法区分；
    一旦误认就会把整篇范文切成两个条目、并让真正的机构名变成"0 字空条目"
    （实测 2018 浙江 A 第 3 题：`# 做隐形冠军` 被当成来源，`### 粉笔` 反成 0 字空条目）。
    而 `# 【先声夺人】`、`# 【qx】` 这种**带括号的 H1** 是名副其实的机构标签，故放行。
    机构标签的惯例写法是 H2–H6（`## 粉笔`／`### 站长`）。
    """
    if not name or not NAME_OK_RE.match(name) or DENY_NAME_RE.match(name):
        return False
    if hashes is not None and hashes < 2 and not wrapped:
        return False
    core = name.strip()
    if len(core) > 8 or " " in core or "　" in core or "·" in core:
        return False
    return len(core) >= 2


def header_candidate(line):
    """判断一行是否是"来源标签行"。

    返回 (label, rest, is_known) 或 None。
    is_known=False 表示"白名单未命中但形态像人名标签"（如 ### 新职途）。
    """
    raw = line.strip()
    if not raw:
        return None
    heading = HEADING_RE.match(raw)
    if heading:
        body = heading.group(2).strip()
        wm = _WRAPPED_RE.match(body)
        wrapped = bool(wm)
        if wm:
            body = wm.group(1).strip()     # `【先声夺人】` → `先声夺人`
        m = re.match(r"^([^：:]{1,24})[：:]\s*(.*)$", body)
        if m:
            name, rest = m.group(1).strip(), m.group(2).strip()
        else:
            name, rest = body, ""
        known = canon_label(name)
        if known:
            return (known, rest, True)
        if _unknown_name_ok(name, len(heading.group(1)), wrapped):
            return (name, rest, False)
        return None
    bold = BOLD_RE.match(raw)
    if bold:
        name = bold.group(1).strip().strip("：: 　").strip()
        rest = bold.group(2).strip()
        known = canon_label(name)
        if known:
            return (known, rest, True)
        if _unknown_name_ok(name):
            return (name, rest, False)
        return None
    m = COLON_NAME_RE.match(raw)
    if m:
        name, rest = m.group(1).strip(), m.group(2).strip()   # 行内：`某笔：1. …`
        known = canon_label(name)
        if known:
            return (known, rest, True)
        return None
    if PLAIN_NUM_RE.match(raw):
        return None
    # 无 # / ** / 冒号：仅白名单精确命中才认（如单独一行 `站长`）
    known = canon_label(raw)
    if known:
        return (known, "", True)
    # 行首 8 字内含白名单名（如 `福州青云端公考徐阳老师 298 字 …`）
    for k in _WL_KEYS:
        if len(k) >= 3:
            idx = raw.find(k)
            if 0 <= idx <= 8:
                prefix = raw[:idx]
                if prefix.strip() and (any(ch.isdigit() for ch in prefix) or
                                       PLAIN_NUM_RE.match(raw)):
                    continue
                rest = raw[idx + len(k):].strip(" 　、,，:：-—")
                return (WHITELIST[k], rest, True)
    return None


class Unit(object):
    """一个"来源条目"：整个文件，或文件内某一家机构/名师的一段口径。"""

    __slots__ = ("label", "question", "text", "chars", "file", "rel", "section_no",
                 "known", "empty")

    def __init__(self, label, question, text, file, rel, section_no, known=True):
        self.label = label
        self.question = question
        self.text = text
        self.chars = len(WS_RE.sub("", text))
        self.file = file
        self.rel = rel
        self.section_no = section_no
        self.known = known
        self.empty = self.chars == 0

    @property
    def qkey(self):
        return self.question if self.question is not None else 0

    @property
    def paper(self):
        """所在目录（= 一张卷）——分组按【卷目录 + 题号】，避免把不同年份卷的同号题混在一起。"""
        d = os.path.dirname(self.rel)
        return d if d else "."


def read_text(path):
    """读文本：utf-8-sig → gbk → latin-1（带提示）。返回 (text, encoding, warning)。"""
    with open(path, "rb") as fh:
        data = fh.read()
    for enc in ("utf-8-sig", "gbk"):
        try:
            return data.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    warn = "编码无法按 utf-8/gbk 解码，已按 latin-1 读取（内容可能有误）：%s" % path
    return data.decode("latin-1"), "latin-1", warn


def collect_files(paths):
    """收集待分析文件（目录递归找 .txt/.md，跳过 README 与隐藏文件）。"""
    files, warnings, missing, empty_dirs = [], [], [], []
    for p in paths:
        ap = os.path.abspath(p)
        if os.path.isdir(ap):
            found = []
            for dirpath, dirnames, filenames in os.walk(ap):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for fn in sorted(filenames):
                    if fn.startswith("."):
                        continue
                    if not fn.lower().endswith((".txt", ".md")):
                        continue
                    if fn.lower() in ("readme.md", "readme.txt"):
                        continue
                    found.append(os.path.join(dirpath, fn))
            if not found:
                empty_dirs.append(p)
            files.extend(found)
        elif os.path.isfile(ap):
            files.append(ap)
        else:
            missing.append(p)
    seen, uniq = set(), []
    for f in files:
        k = os.path.normcase(os.path.abspath(f))
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq, warnings, missing, empty_dirs


def _drop_titles_under_institutions(heads):
    """丢掉"机构标签正下方、更深一级的未知标签"——那通常是范文/公文标题，不是来源标签。

    实测两类真实排版：
      a) `### 粉笔` + `# 做隐形冠军`（H1 标题）——已被 `_unknown_name_ok` 的 H1 规则挡掉；
      b) `# 【粉笔】` + `## 流动与新生`（H1 机构 ＋ H2 标题）——机构名在 H1，
         标题在 H2；若把标题当来源，就会丢掉真正的机构名（国考多份卷即此排版）。

    只在三条同时满足时生效：前一条是**白名单命中**的机构标签 ＋ 本条是**更深一级**的未知标签
    ＋ 两行相邻（间隔 ≤2 行）。这样不会误杀 `## 粉笔` 后面紧跟的 `## 华图`（同级）。
    """
    out = []
    for idx, h in enumerate(heads):
        if idx > 0:
            prev = heads[idx - 1]
            if (not h[3]) and prev[3] and h[4] > prev[4] and (h[0] - prev[0]) <= 2:
                continue
        out.append(h)
    return out


def build_units(path, rel, text, opts, warnings):
    """把一个文件切成若干"来源条目"。"""
    lines = text.split("\n")
    heads = []          # (line_idx, label, rest, known, level)
    for i, ln in enumerate(lines):
        cand = header_candidate(ln)
        if cand:
            m = HEADING_RE.match(ln.strip())
            lvl = len(m.group(1)) if m else 0
            heads.append((i, cand[0], cand[1], cand[2], lvl))
    heads = _drop_titles_under_institutions(heads)
    file_q = extract_question(os.path.basename(path))
    base = os.path.basename(path)
    split = not opts.no_split
    sections = []
    for n, (i, label, rest, known, _lvl) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        body = ([rest] if rest else []) + lines[i + 1:end]
        body_txt = "\n".join(body).strip()
        sections.append({"label": label, "start": i, "text": body_txt, "known": known})

    # 是否需要切分：≥2 个标签；或 1 个标签但覆盖了文件大半内容
    whole = WS_RE.sub("", text)
    if split and sections:
        covered = sum(len(WS_RE.sub("", s["text"])) for s in sections)
        if not (len(sections) >= 2 or (len(whole) and covered >= 0.5 * len(whole))):
            sections = []
    if not (split and sections):
        label = base
        units = [Unit(label, file_q, text, path, rel, 0)]
        _warn_empty(units, warnings)
        return units, heads

    units = []
    used = {}
    qmark = []
    for i, ln in enumerate(lines):
        m = Q_HEAD_RE.match(ln.strip())
        if m:
            q = cn_to_int(m.group(1))
            if q is not None:
                qmark.append((i, q))
    for idx, s in enumerate(sections):
        q = file_q
        for (li, qq) in qmark:
            if li <= s["start"]:
                q = qq
            else:
                break
        name = s["label"]
        used[name] = used.get(name, 0) + 1
        label = "%s·%s" % (base, name)
        if used[name] > 1:
            label += "#%d" % used[name]
        u = Unit(label, q, s["text"], path, rel, idx + 1, s["known"])
        units.append(u)
        if file_q is not None and q is not None and q != file_q:
            warnings.append("⚠ 文件 %s 内问答小节属第 %s 题，与文件名题号（第 %s 题）不一致，"
                            "已按文内题号归组：%s" % (base, q, file_q, label))
    if not units:
        units = [Unit(base, file_q, text, path, rel, 0)]
    _warn_empty(units, warnings)
    _warn_possible_missed_labels(units, warnings)
    return units, heads


SUSPECT_LABEL_RE = re.compile(
    r"^([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z ·\-—_]{1,11})[：:]\s*\S")


def _warn_possible_missed_labels(units, warnings):
    """条目内部若出现"机构名＋冒号＋内容"的行，提示可能漏识别标签（会低估来源数）。

    典型：`袁东老师：这句话指…` 因为旧白名单里没有"袁东老师"而被并入上一条。
    """
    for u in units:
        if not u.section_no:
            continue
        for ln in u.text.split("\n"):
            s = ln.strip().lstrip("#").strip()
            m = SUSPECT_LABEL_RE.match(s)
            if not m:
                continue
            cand = m.group(1).strip()
            if any(ch.isdigit() for ch in cand) or len(cand) < 2:
                continue
            if cand == u.label.split("·")[-1].split("#")[0]:
                continue
            looks_like = (canon_label(cand) is not None
                          or cand.endswith(("老师", "教育", "公考", "申论")))
            if looks_like:
                warnings.append(
                    "⚠ 疑似漏识别来源标签：%s 内含“%s：”（可能把另一家口径并入了本条，"
                    "请核对白名单或改用 --no-split）" % (u.label, cand))
                break


def _warn_empty(units, warnings):
    for u in units:
        if u.empty:
            warnings.append("⚠ 空条目（0 字，未纳入同源判定）：%s" % u.label)


def strip_common_prefix(units):
    """同一题下若多数条目以统一前缀开头，则去掉该前缀行（仅影响相似度计算）。"""
    if len(units) < 2:
        return 0
    hit = 0
    for u in units:
        first = u.text.strip().split("\n", 1)[0].strip() if u.text.strip() else ""
        if any(r.match(first) for r in PREFIX_RES):
            hit += 1
    if hit * 2 < len(units):
        return 0
    for u in units:
        first, sep, rest = u.text.strip().partition("\n")
        if any(r.match(first.strip()) for r in PREFIX_RES):
            u.text = rest if sep else re.sub(r"^.*?(?=\S)", "", first, count=1)
    return hit


def norm(text):
    return WS_RE.sub("", text)


def ratio_of(a, b, no_autojunk=False):
    if not a or not b:
        return None
    sm = difflib.SequenceMatcher(None, a, b, autojunk=not no_autojunk)
    return sm.ratio()


def group_units(units, threshold, no_autojunk=False):
    """按相似度阈值并查集分组；返回 (groups, pairs)。空条目各自成组、不参与判定。"""
    real = [u for u in units if not u.empty]
    n = len(real)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            r = ratio_of(norm(real[i].text), norm(real[j].text), no_autojunk)
            if r is None:
                continue
            pairs.append({"a": real[i].label, "b": real[j].label, "ratio": round(r, 4),
                          "i": i, "j": j})
            if r >= threshold:
                union(i, j)
    buckets = {}
    for i, u in enumerate(real):
        buckets.setdefault(find(i), []).append(u)
    groups = []
    for root, members in buckets.items():
        idxs = [real.index(m) for m in members]
        inner = [p for p in pairs if p["i"] in idxs and p["j"] in idxs]
        mx = max([p["ratio"] for p in inner], default=None)
        groups.append({"members": members, "max_ratio": mx, "inner": inner})
    groups.sort(key=lambda g: (-len(g["members"]), -(g["max_ratio"] or 0)))
    empties = [u for u in units if u.empty]
    return groups, pairs, empties


def kind_of(r, near, same):
    if r is None:
        return "single"
    return "same" if r >= same else ("near" if r >= near else "below")


KIND_CN = {"same": "同源转抄", "near": "疑似同源", "single": "独立", "below": "低于阈值"}


def disp_width(s):
    w = 0
    for ch in s:
        w += 2 if (u"\u1100" <= ch <= u"\u115f" or u"\u2e80" <= ch <= u"\ua4cf"
                   or u"\uac00" <= ch <= u"\ud7a3" or u"\uf900" <= ch <= u"\ufaff"
                   or u"\ufe30" <= ch <= u"\ufe6f" or u"\uff00" <= ch <= u"\uff60"
                   or u"\uffe0" <= ch <= u"\uffe6") else 1
    return w


def pad(s, width):
    return s + " " * max(0, width - disp_width(s))


def line_writer(stream):
    """把 write(str) 包装成自动换行的输出函数（人类可读输出统一走它）。"""
    def w(s=""):
        stream.write(s)
        stream.write("\n")
    return w


def render_question(q, units, groups, pairs, empties, opts, out, title=None):
    qlabel = title or (("第%d题" % q) if q is not None else "未分题")
    fcount = len(set(u.file for u in units))
    ucount = len(units)
    split_mode = any(u.section_no for u in units)
    # 口径（用户 2026-09 决定）：**0 字空条目不计入独立来源家数**——
    # 它与“是否命中某个采分点”无关，若计入会让“1 家真答案 + 1 个空占位文件”跨过
    # “≥2 家独立”的门槛，从而误判出共识点。
    indep = len(groups)
    if not split_mode:
        head = "=== %s（%d 个文件 → 独立来源 %d 家）===" % (qlabel, ucount, indep)
    else:
        head = "=== %s（%d 个来源条目 / %d 个文件 → 独立来源 %d 家）===" % (
            qlabel, ucount, fcount, indep)
    out(head)
    if q is None:
        out("⚠ 以下条目未能从文件名/文件内标题识别到题号，已单独归组"
            "（建议在文件名或文件头补齐“第N题”）：")
    noun = "来源条目" if split_mode else "文件"
    for gi, g in enumerate(groups, 1):
        members = sorted(g["members"], key=lambda u: (-u.chars, u.label))
        if len(members) == 1:
            out("[组%d] 1 个%s（独立）" % (gi, noun))
        else:
            k = kind_of(g["max_ratio"], opts.near, opts.same)
            out("[组%d] %d 个%s（%s，最高相似度 %.2f）" % (
                gi, len(members), noun, KIND_CN[k], g["max_ratio"]))
        shown = []
        for m in members:
            if not shown:
                out("  - %s（%d 字）" % (m.label, m.chars))
            else:
                best, bestr = None, None
                for p in g["inner"]:
                    a, b = p["a"], p["b"]
                    if (a == m.label and b in shown) or (b == m.label and a in shown):
                        if bestr is None or p["ratio"] > bestr:
                            bestr, best = p["ratio"], (a if b == m.label else b)
                if bestr is None:
                    out("  - %s（%d 字）" % (m.label, m.chars))
                else:
                    out("  - %s（%d 字）  ↔ 与 %s 相似度 %.2f（%s）" % (
                        m.label, m.chars, best, bestr,
                        KIND_CN[kind_of(bestr, opts.near, opts.same)]))
            shown.append(m.label)
    for e in empties:
        out("[空] 1 个来源条目（空文件，**不计入独立来源家数**）")
        out("  - %s（0 字）" % e.label)

    # 知情提示：同一机构出现在不同组时**不合并**（用户 2026-09 口径：
    # 同一机构的不同答案视为两份不同答案），但把它标出来，便于人工判断是否要采信。
    by_org = {}
    for gi, g in enumerate(groups, 1):
        for m in g["members"]:
            org = getattr(m, "org", None) or (m.label.split("·")[-1] if "·" in m.label else None)
            if org:
                # 同一机构的多份答案会带去重后缀（`中公` / `中公#2`）——提示时要认成同一机构，
                # 否则"同一机构出现在多个组"这条知情提示会漏报（用户 2026-09 口径：只提示、不合并）。
                org = re.sub(r"#\d+$", "", org)
                by_org.setdefault(org, set()).add(gi)
    multi = {o: gs for o, gs in by_org.items() if len(gs) > 1}
    if multi:
        out("⚠ 同一机构出现在多个组（**按当前口径不合并，各计 1 家**，仅供参考）：")
        for o, gs in sorted(multi.items()):
            out("  - %s：组%s" % (o, "、".join(str(x) for x in sorted(gs))))

    if not opts.quiet:
        shown_pairs = [p for p in pairs if p["ratio"] >= opts.near]
        out("—— 相似度明细（降序，仅列 ≥ near=%.2f 的对）：" % opts.near)
        if not shown_pairs:
            out("  （无：本题目条目两两相似度均低于 near 阈值）")
        for p in sorted(shown_pairs, key=lambda x: -x["ratio"]):
            out("  %s ↔ %s = %.2f（%s）" % (p["a"], p["b"], p["ratio"],
                                           KIND_CN[kind_of(p["ratio"], opts.near, opts.same)]))
    shorts = [u for u in units if 0 < u.chars < opts.min_chars]
    if shorts:
        out("⚠ 疑为节选（<%d 字）：%s" % (
            opts.min_chars, "、".join("%s（%d 字）" % (u.label, u.chars)
                                      for u in sorted(shorts, key=lambda x: x.chars))))
    out("")


def render_summary(rows, opts, out, total_files):
    out("—— 总汇总 ——")
    multi = len(set(r.get("paper", "") for r in rows)) > 1
    headers = (["试卷/目录"] if multi else []) + \
              ["题目", "文件数", "独立来源家数", "最大组规模", "疑为节选文件数"]
    table = []
    for r in rows:
        cells = ([r["paper"]] if multi else []) + \
                [r["label"], str(r["units"]), str(r["independent"]),
                 str(r["max_group"]), str(r["short"])]
        table.append(cells)
    widths = [disp_width(h) for h in headers]
    for cells in table:
        for i, c in enumerate(cells):
            widths[i] = max(widths[i], disp_width(c))
    tot = (["合计"] if not multi else ["合计", ""]) + \
          [str(total_files), str(sum(r["independent"] for r in rows)),
           str(max([r["max_group"] for r in rows], default=0)),
           str(sum(r["short"] for r in rows))]
    for i, c in enumerate(tot):
        widths[i] = max(widths[i], disp_width(c))
    line = " | ".join(pad(h, widths[i]) for i, h in enumerate(headers))
    out(line)
    out("-" * disp_width(line))
    for cells in table:
        out(" | ".join(pad(c, widths[i]) for i, c in enumerate(cells)))
    out(" | ".join(pad(c, widths[i]) for i, c in enumerate(tot)))
    if any(r["units"] != r["files"] for r in rows):
        out("注：“文件数”列为来源条目数（文件内按机构/名师标签切分，--no-split 可关闭）；"
            "标题中的“/ N 个文件”为实际文件数。")
    else:
        out("注：本卷每个文件即一个来源条目（文件内未识别到机构/名师标签）。")


def render_sensitivity(group_src, opts, out):
    out("—— 阈值敏感性（各阈值下的独立来源家数）——")
    ths = [0.50, 0.60, 0.70, 0.80, 0.90]
    out(pad("题目", 13) + "| " + " | ".join("%.2f" % t for t in ths))
    for title, units in group_src:
        vals = []
        for t in ths:
            g, _p, e = group_units(units, t, opts.no_autojunk)
            vals.append(len(g) + len(e))
        out("%s | %s" % (pad(title[-30:], 12), " | ".join("%4d" % v for v in vals)))
    out("")


def _warn_length_outlier(qlabel, units, warnings):
    """某条明显长于同题其他条目 → 提示可能含多篇范文或漏识别标签（仅提示，不影响分组）。"""
    ch = sorted(u.chars for u in units if not u.empty)
    if len(ch) < 3:
        return
    med = ch[len(ch) // 2]
    if med < 150:
        return
    for u in units:
        if not u.empty and u.chars > 2.0 * med:
            warnings.append("⚠ %s 的“%s”明显长于同题其他条目（%d 字 vs 中位 %d 字）："
                            "多为同一来源的多个参考答案版本或多篇范文（不影响来源计数），"
                            "但也可能是漏识别标签，请人工确认" % (qlabel, u.label,
                                                        u.chars, med))


def analyze(paths, opts):
    """核心：返回 (question_blocks, summary, warnings)。"""
    warnings = []
    files, _w, missing, empty_dirs = collect_files(paths)
    for d in empty_dirs:
        warnings.append("⚠ 目录内未找到 .txt/.md 参考答案文件：%s" % d)
    units_all = []
    for f in files:
        text, enc, warn = read_text(f)
        if warn:
            warnings.append("⚠ " + warn)
        elif enc != "utf-8-sig":
            warnings.append("ℹ 文件按 %s 解码：%s" % (enc, os.path.basename(f)))
        try:
            rel = os.path.relpath(f, os.getcwd())
        except ValueError:          # 不同盘符（如临时目录在 C:，工作区在 D:）
            rel = os.path.abspath(f)
        us, _heads = build_units(f, rel, text, opts, warnings)
        units_all.extend(us)
    if not files:
        return [], {"files": 0, "units": 0, "questions": 0, "independent_total": 0,
                    "short_units": 0, "empty_units": 0, "table": [],
                    "near": opts.near, "same": opts.same,
                    "min_chars": opts.min_chars, "missing": missing,
                    "filtered_no_question": []}, warnings

    by_q = {}
    for u in units_all:
        by_q.setdefault((u.paper, u.question), []).append(u)
    keys = sorted(by_q.keys(), key=lambda k: (k[0], 0 if k[1] is not None else 1,
                                              k[1] if k[1] is not None else 0))
    multi_paper = len(set(k[0] for k in keys)) > 1

    blocks, table, group_src = [], [], []
    filtered_no_question = []      # --question 模式下被过滤掉、且题号识别不出的条目
    for (paper, q) in keys:
        units = by_q[(paper, q)]
        if opts.question is not None and q != opts.question:
            # 题号为 None（文件名/文内标题都没题号）的条目会被 --question 过滤掉。
            # 必须记录并告警，否则"家数少算"没有任何提示（本版修复，见 small-questions.md
            # 第四节第 3 节"四条复核硬规则"第 4 条）。
            if q is None:
                filtered_no_question.extend(units)
            continue
        strip_common_prefix(units)
        groups, pairs, empties = group_units(units, opts.near, opts.no_autojunk)
        label = ("第%d题" % q) if q is not None else "未分题"
        title = ("%s · %s" % (paper, label)) if multi_paper else label
        blocks.append({"question": q, "label": label, "paper": paper, "title": title,
                       "units": units, "groups": groups,
                       "pairs": pairs, "empties": empties})
        group_src.append((title, units))
        table.append({
            "question": q, "label": label, "paper": paper, "title": title,
            "files": len(set(u.file for u in units)),
            "units": len(units),
            "independent": len(groups),   # 0 字空条目不计入家数（见 render_question 注释）
            "max_group": max([len(g["members"]) for g in groups] + [1]),
            "short": len([u for u in units if 0 < u.chars < opts.min_chars]),
        })
        _warn_length_outlier(title, units, warnings)
    if filtered_no_question:
        labels = list(dict.fromkeys(u.label for u in filtered_no_question))
        warnings.append(
            "⚠ 有 %d 个来源条目未识别题号，已被 --question 过滤掉，未计入本题家数：%s"
            "（建议：先跑一次不带 --question 的全量输出，确认每份答案都归到了题号）"
            % (len(filtered_no_question), "、".join(labels)))
    summary = {
        "papers": sorted(set(k[0] for k in keys)),
        "files": len(files),
        "units": len(units_all),
        "questions": len(blocks),
        "independent_total": sum(r["independent"] for r in table),
        "short_units": sum(r["short"] for r in table),
        "empty_units": len([u for u in units_all if u.empty]),
        "table": table,
        "near": opts.near, "same": opts.same, "min_chars": opts.min_chars,
        "missing": missing,
        "filtered_no_question": [
            {"label": u.label, "file": u.rel, "paper": u.paper, "chars": u.chars}
            for u in filtered_no_question],
    }
    return blocks, summary, warnings


def render_human(blocks, summary, warnings, opts, out):
    if not blocks:
        out("未找到可分析的参考答案条目（检查路径与 --question 过滤条件）。")
        for w in warnings:
            out(w)
        return
    for b in blocks:
        render_question(b["question"], b["units"], b["groups"], b["pairs"],
                        b["empties"], opts, out, b.get("title"))
    render_summary(summary["table"], opts, out,
                   sum(r["units"] for r in summary["table"]))
    if opts.sensitivity:
        render_sensitivity([(b.get("title") or b["label"], b["units"]) for b in blocks],
                           opts, out)
    if warnings:
        out("—— 提示 ——")
        for w in warnings:
            out(w)
    out("说明：分组依据为文本相似度（经验校准 L4，非官方口径），"
        "只用于判定“几家独立来源”，不参与给分。")


def render_json(blocks, summary, warnings, opts, out):
    qs = []
    for b in blocks:
        groups = []
        for g in b["groups"]:
            members = sorted(g["members"], key=lambda u: (-u.chars, u.label))
            groups.append({
                "members": [{"label": m.label, "file": m.rel, "chars": m.chars}
                            for m in members],
                "max_ratio": g["max_ratio"],
                "kind": kind_of(g["max_ratio"], opts.near, opts.same),
            })
        qs.append({
            "question": b["question"],
            "label": b["label"],
            "paper": b.get("paper"),
            "title": b.get("title") or b["label"],
            "files": len(b["units"]) if len(set(u.file for u in b["units"])) != len(b["units"])
                     else len(b["units"]),
            "units": len(b["units"]),
            "source_files": len(set(u.file for u in b["units"])),
            "independent": len(b["groups"]),   # 0 字空条目不计入家数（与人类可读输出口径一致）
            "max_group": max([len(g["members"]) for g in b["groups"]] + [1]),
            "groups": groups,
            "pairs": [{"a": p["a"], "b": p["b"], "ratio": p["ratio"],
                       "kind": kind_of(p["ratio"], opts.near, opts.same)}
                      for p in sorted(b["pairs"], key=lambda x: -x["ratio"])],
            "short_files": [{"label": u.label, "file": u.rel, "chars": u.chars}
                            for u in b["units"] if 0 < u.chars < opts.min_chars],
            "empty_files": [{"label": u.label, "file": u.rel} for u in b["empties"]],
        })
    payload = {
        "tool": "ref_independence.py", "version": VERSION,
        "note": "仅用于参考答案之间的同源判定，不参与给分（L4 经验校准）",
        "thresholds": {"same": opts.same, "near": opts.near, "min_chars": opts.min_chars,
                       "split_sections": not opts.no_split},
        "questions": qs,
        "summary": summary,
        # --question 模式下被过滤掉、且题号识别不出的条目（家数少算必须可见）
        "filtered_no_question": summary.get("filtered_no_question", []),
        "warnings": warnings,
    }
    out(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 自检
def run_selftest():
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        print("%-6s %s%s" % ("OK" if ok else "FAIL", name,
                             ("   [%s]" % detail) if detail else ""))

    base = ("邻里中心建设要立足群众需求，通过走访座谈了解实际诉求，"
            "引入优质商业品牌与养老机构，街道主导、各方参与议定方案，"
            "增设便民服务与潮牌店铺，打造文化背景墙与阅读空间，"
            "邀请专业老师开设艺术课程，推出心愿驿站收集群众诉求，"
            "建设康养服务中心提供助餐助浴托养服务，让居民找到归属感。")

    def mkpoints(prefix, n, start=1):
        return "".join(
            "%s%d、加强宣传引导，提升群众知晓率与参与度，营造良好氛围，"
            "推动各项举措落地见效，切实增强获得感与幸福感。" % (prefix, i)
            for i in range(start, start + n))

    unrelated = ("全球气候变化背景下，海洋酸化加剧，珊瑚礁生态系统面临退化风险；"
                 "科研团队利用卫星遥感与浮标阵列监测海温异常，"
                 "并联合渔业部门调整捕捞配额，试图在生态保护与产业收益之间取得平衡。")

    with tempfile.TemporaryDirectory(prefix="ref_ind_selftest_") as td:
        opts = Options()

        # 1) 完全相同 → 1.0 / 同一组 / 独立来源 1
        d1 = os.path.join(td, "t1")
        os.makedirs(d1)
        open(os.path.join(d1, "第1题-粉笔.txt"), "w", encoding="utf-8").write(base)
        open(os.path.join(d1, "第1题-中公.txt"), "w", encoding="utf-8").write(base)
        b, s, _w = analyze([d1], opts)
        r = b[0]["pairs"][0]["ratio"] if b and b[0]["pairs"] else None
        check("自检1 完全相同文本 → ratio=1.0、同组、独立来源=1",
              r == 1.0 and len(b[0]["groups"]) == 1 and b[0]["groups"][0]["max_ratio"] == 1.0
              and s["table"][0]["independent"] == 1, "ratio=%s 独立=%s" % (
                  r, s["table"][0]["independent"] if s["table"] else "?"))

        # 2) 高度改写（0.6–0.8）→ 同一组（near）
        A = mkpoints("要点", 20)
        B = None
        r2 = None
        for k in range(19, 0, -1):
            cand = mkpoints("要点", k) + mkpoints("另议", 20 - k)
            rr = ratio_of(norm(A), norm(cand))
            if rr is not None and rr < opts.same:
                B, r2 = cand, rr
                break
        d2 = os.path.join(td, "t2")
        os.makedirs(d2)
        open(os.path.join(d2, "第1题-a.txt"), "w", encoding="utf-8").write(A)
        open(os.path.join(d2, "第1题-b.txt"), "w", encoding="utf-8").write(B or A)
        b2, s2, _w = analyze([d2], opts)
        ok2 = (r2 is not None and opts.near <= r2 < opts.same
               and len(b2[0]["groups"]) == 1
               and abs(b2[0]["groups"][0]["max_ratio"] - r2) < 1e-4)
        check("自检2 高度改写(重叠 0.6–0.8) → 归为同一组(near)",
              ok2, "ratio=%.4f 独立=%s" % (r2 or -1, s2["table"][0]["independent"]))

        # 3) 完全无关 → 独立来源 2
        d3 = os.path.join(td, "t3")
        os.makedirs(d3)
        open(os.path.join(d3, "第1题-a.txt"), "w", encoding="utf-8").write(base)
        open(os.path.join(d3, "第1题-b.txt"), "w", encoding="utf-8").write(unrelated)
        b3, s3, _w = analyze([d3], opts)
        r3 = b3[0]["pairs"][0]["ratio"] if b3 and b3[0]["pairs"] else None
        check("自检3 完全无关文本 → 独立来源=2",
              len(b3[0]["groups"]) == 2 and s3["table"][0]["independent"] == 2
              and r3 is not None and r3 < opts.near, "ratio=%.4f" % (r3 or -1,))

        # 4) 题号识别：第1题.txt / 第一题-粉笔.md / 第2题.txt → 两组
        d4 = os.path.join(td, "t4")
        os.makedirs(d4)
        open(os.path.join(d4, "第1题.txt"), "w", encoding="utf-8").write(base)
        open(os.path.join(d4, "第一题-粉笔.md"), "w", encoding="utf-8").write(unrelated)
        open(os.path.join(d4, "第2题.txt"), "w", encoding="utf-8").write(base[::-1])
        b4, s4, _w = analyze([d4], opts)
        qs4 = sorted(x["question"] for x in s4["table"])
        check("自检4 题号识别 第1题/第一题-粉笔/第2题 → 分出 1、2 两组",
              qs4 == [1, 2] and s4["table"][0]["units"] == 2
              and s4["table"][1]["units"] == 1, "题号=%s" % qs4)

        # 5) 中文数字与阿拉伯数字混用
        d5 = os.path.join(td, "t5")
        os.makedirs(d5)
        open(os.path.join(d5, "第 3 题-华图.txt"), "w", encoding="utf-8").write(base)
        open(os.path.join(d5, "第三题.txt"), "w", encoding="utf-8").write(unrelated)
        open(os.path.join(d5, "第二十题.txt"), "w", encoding="utf-8").write(base + unrelated)
        open(os.path.join(d5, "第2题_小马哥.txt"), "w", encoding="utf-8").write(unrelated[::-1])
        b5, s5, _w = analyze([d5], opts)
        qs5 = sorted(x["question"] for x in s5["table"])
        check("自检5 中文/阿拉伯数字混用 → 题号 2、3、20",
              qs5 == [2, 3, 20] and cn_to_int("二十") == 20 and extract_question("第 3 题-华图.txt") == 3,
              "题号=%s 二十→%s" % (qs5, cn_to_int("二十")))

        # 6) 空文件 / 单文件目录 不崩
        d6 = os.path.join(td, "t6")
        os.makedirs(d6)
        open(os.path.join(d6, "第1题-空.txt"), "w", encoding="utf-8").write("")
        open(os.path.join(d6, "第1题-有.txt"), "w", encoding="utf-8").write(base)
        d7 = os.path.join(td, "t7")
        os.makedirs(d7)
        open(os.path.join(d7, "第2题-唯一.txt"), "w", encoding="utf-8").write(base)
        d8 = os.path.join(td, "empty_dir")
        os.makedirs(d8)
        try:
            b6, s6, w6 = analyze([d6], opts)
            b7, s7, _w7 = analyze([d7], opts)
            b8, s8, w8 = analyze([d8], opts)
            ok6 = (len(b6) == 1 and len(b6[0]["empties"]) == 1 and s6["empty_units"] == 1
                   and any("空条目" in x for x in w6)
                   and s7["table"][0]["units"] == 1 and s7["table"][0]["independent"] == 1
                   and (not b8) and any("未找到" in x for x in w8))
            check("自检6 空文件/单文件目录/空目录 不崩且有提示", ok6,
                  "空=%s 单=%s 空目录块=%s" % (s6["empty_units"], s7["table"][0]["units"], len(b8)))
        except Exception as exc:      # pragma: no cover
            check("自检6 空文件/单文件目录/空目录 不崩且有提示", False, repr(exc))

        # 7) 文件内多机构切分（本脚本的关键能力）
        d9 = os.path.join(td, "t9")
        os.makedirs(d9)
        open(os.path.join(d9, "第1题.txt"), "w", encoding="utf-8").write(
            "## 第一题：请概括材料\n\n要求：不超过 300 字。\n\n### 粉笔\n\n" + base +
            "\n\n### 华图\n\n" + base + "\n\n某笔：1. 加强宣传引导，提升群众知晓率。\n\n"
            + "站长\n\n" + unrelated + "\n")
        b9, s9, _w9 = analyze([d9], opts)
        labels9 = [u.label for u in b9[0]["units"]] if b9 else []
        ok9 = (s9["table"][0]["units"] == 4 and s9["table"][0]["files"] == 1
               and s9["table"][0]["independent"] == 3)
        check("自检7 单文件内多机构切分 → 4 条目/1 文件/独立 3 家", ok9,
              "条目=%s 独立=%s" % (labels9, s9["table"][0]["independent"] if s9["table"] else "?"))

        # 8) JSON 可序列化且结构含必需键
        import io as _io
        buf = _io.StringIO()
        render_json(b9, s9, _w9, opts, buf.write)
        try:
            data = json.loads(buf.getvalue())
            okj = ("questions" in data and "summary" in data
                   and set(["question", "files", "independent", "groups", "pairs",
                            "short_files"]) <= set(data["questions"][0].keys()))
        except Exception as exc:      # pragma: no cover
            okj = False
        check("自检8 --json 结构含 question/files/independent/groups/pairs/short_files", okj)

        # 9) 【本版修复】--question 模式下，题号识别不出的条目被过滤掉时必须告警并记录
        #    （旧版直接 continue，家数少算且毫无提示；见 small-questions.md 第四节第 3 节）
        d10 = os.path.join(td, "t10")
        os.makedirs(d10)
        open(os.path.join(d10, "第1题-粉笔.txt"), "w", encoding="utf-8").write(base)
        open(os.path.join(d10, "第1题-中公.txt"), "w", encoding="utf-8").write(unrelated)
        open(os.path.join(d10, "整卷答案-无题号.txt"), "w", encoding="utf-8").write(base)
        q_opts = Options(question=1)
        b10, s10, w10 = analyze([d10], q_opts)
        fq10 = s10.get("filtered_no_question") or []
        buf10 = _io.StringIO()
        render_json(b10, s10, w10, q_opts, buf10.write)
        j10 = json.loads(buf10.getvalue())
        ok10 = (len(b10) == 1 and s10["table"][0]["independent"] == 2
                and len(fq10) == 1 and fq10[0]["label"] == "整卷答案-无题号.txt"
                and any("未识别题号" in x and "已被 --question 过滤掉" in x for x in w10)
                and len(j10.get("filtered_no_question") or []) == 1
                and any("未识别题号" in x for x in (j10.get("warnings") or [])))
        check("自检9 --question 过滤掉的“未识别题号”条目必须告警并记入 filtered_no_question",
              ok10, "过滤=%d 家数=%s" % (len(fq10), s10["table"][0]["independent"]))

        # 10) 【本版修复】H1 范文标题不得被当成来源标签；"（参考答案 N）"式尾部限定要剥掉
        #     复现 2018 浙江 A 第 3 题：`### 站长` + `# 范文标题` + `### 中公（参考答案 1）` 混排。
        d11 = os.path.join(td, "t11")
        os.makedirs(d11)
        open(os.path.join(d11, "第3题.txt"), "w", encoding="utf-8").write(
            "## 第三题：以“隐形冠军”为话题写一篇议论文\n\n### 站长\n\n# 隐形冠军 走向领军\n\n"
            + base + "\n\n### 中公（参考答案 1）\n\n# 培养“隐形冠军”助推社会发展\n\n" + unrelated
            + "\n\n### 中公（参考答案 2）\n\n# 打造隐形冠军 铸就专业“巨头”\n\n" + base.replace("宣传", "推广")
            + "\n")
        b11, s11, w11 = analyze([d11], opts)
        labels11 = [u.label for u in b11[0]["units"]] if b11 else []
        empt11 = b11[0]["empties"] if b11 else []
        ok11 = (s11["table"][0]["units"] == 3 and s11["table"][0]["files"] == 1
                and not empt11
                and not any("做" in str(l) or "走向领军" in str(l) for l in labels11))
        check("自检10 H1 范文标题不得当成来源标签；“（参考答案 N）”尾部限定要剥掉",
              ok11, "条目=%s 空=%d" % (labels11, len(empt11)))

    failed = [r for r in results if not r[1]]
    print("-" * 60)
    print("selftest: %s" % ("all passed" if not failed else "FAILED (%d)" % len(failed)))
    return 0 if not failed else 1


class Options(object):
    def __init__(self, question=None, same=DEFAULT_SAME, near=DEFAULT_NEAR,
                 min_chars=DEFAULT_MIN_CHARS, quiet=False, no_split=False,
                 sensitivity=False, no_autojunk=False, **kw):
        self.question = question
        self.same = same
        self.near = near
        self.min_chars = min_chars
        self.quiet = quiet
        self.no_split = no_split
        self.sensitivity = sensitivity
        self.no_autojunk = no_autojunk


def build_parser():
    p = argparse.ArgumentParser(
        prog="ref_independence.py",
        description="参考答案同源检测：用文本相似度分组，输出“独立来源家数 = 分组数”。\n"
                    "只用于参考答案之间判同源，不参与给分。",
        epilog="示例：python ref_independence.py 参考答案/河南/2023河南市级 --question 2\n"
               "      python ref_independence.py 参考答案/河南/2024河南县级 --json\n"
               "      python ref_independence.py --selftest\n"
               "说明：--same/--near 为经验阈值（L4），不参与给分；"
               "本脚本不比较考生作答与参考答案。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", metavar="路径",
                   help="目录（递归找参考答案文件）或若干具体文件")
    p.add_argument("--question", "-q", metavar="N", default=None,
                   help="只分析第 N 题（阿拉伯数字，也接受一~二十）")
    p.add_argument("--same", type=float, default=DEFAULT_SAME, metavar="FLOAT",
                   help="同源转抄阈值，默认 %.2f" % DEFAULT_SAME)
    p.add_argument("--near", type=float, default=DEFAULT_NEAR, metavar="FLOAT",
                   help="疑似同源阈值，默认 %.2f（区间 [near, same) 也归为同一组）" % DEFAULT_NEAR)
    p.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS, metavar="N",
                   help="短于 N 字标记“疑为节选”，默认 %d" % DEFAULT_MIN_CHARS)
    p.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    p.add_argument("--quiet", action="store_true", help="只输出汇总，不逐对列相似度")
    p.add_argument("--selftest", action="store_true", help="内置自检（忽略路径参数）")
    p.add_argument("--no-split", action="store_true",
                   help="关闭文件内按机构/名师标签切分（默认开启）")
    p.add_argument("--sensitivity", action="store_true",
                   help="额外打印阈值敏感性表（0.50–0.90 各阈值的独立来源家数）")
    p.add_argument("--no-autojunk", action="store_true",
                   help="关闭 difflib autojunk 启发式（长文本交叉验证用）")
    p.add_argument("--version", action="version", version="ref_independence.py " + VERSION)
    return p


def main(argv=None):
    _setup_streams()
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.selftest:
        return run_selftest()
    if not ns.paths:
        parser.print_help()
        sys.stderr.write("\n错误：至少需要一个路径（目录或文件）。\n")
        return 2
    if not (0.0 < ns.near <= ns.same <= 1.0):
        parser.error("阈值需满足 0 < --near <= --same <= 1（当前 near=%s same=%s）"
                     % (ns.near, ns.same))
    if ns.min_chars < 0:
        parser.error("--min-chars 不能为负")
    q = ns.question
    if q is not None:
        qv = cn_to_int(str(q))
        if qv is None or qv < 1:
            parser.error("--question 需要正整数或中文数字（一~二十）：%r" % q)
        q = qv
    opts = Options(question=q, same=ns.same, near=ns.near, min_chars=ns.min_chars,
                   quiet=ns.quiet, no_split=ns.no_split, sensitivity=ns.sensitivity,
                   no_autojunk=ns.no_autojunk)

    missing = [p for p in ns.paths if not os.path.exists(p)]
    if missing:
        sys.stderr.write("错误：路径不存在：%s\n" % "、".join(missing))
        return 3

    blocks, summary, warnings = analyze(ns.paths, opts)
    if ns.json:
        out = sys.stdout.write
        render_json(blocks, summary, warnings, opts, out)
        out("\n")
    else:
        render_human(blocks, summary, warnings, opts, line_writer(sys.stdout))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:          # 输出被 head/Select-Object 截断时静默退出
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.stderr.write("已中断\n")
        sys.exit(130)
