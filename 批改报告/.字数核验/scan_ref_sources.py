import os, re, io

ROOT = r"D:\Projtcts\shenlun\参考答案"

BOLD = re.compile(r"^\*\*(?P<name>[^*\n]{1,26})\*\*")
COLON = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9 ()（）\.·\-—_/＋+、]{2,26}[：:]$")
INLINE = re.compile(r"^(?P<name>[\u4e00-\u9fa5A-Za-z0-9 upUP·\-—_]{2,20})[：:]\s*\S")
PLAIN = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9 upUP·\-—\s]{2,14}$")

# 已知机构／名师白名单（含脱敏写法），命中才算"来源标签"
WHITELIST = {
    "粉笔": "粉笔", "某笔": "粉笔（某笔）", "粉笔蒋春旭": "粉笔蒋春旭",
    "华图": "华图", "某图": "华图（某图）",
    "中公": "中公", "某公": "中公（某公）",
    "站长": "站长", "站长参考 1": "站长", "半月谈": "半月谈", "半月谈白鹭": "半月谈白鹭",
    "白鹭": "白鹭", "花木君": "花木君", "申研社": "申研社", "贺冲": "贺冲", "小飞仔": "小飞仔",
    "人须在事上磨": "人须在事上磨", "程诺": "程诺", "单淑玲": "单淑玲", "张嘉庆": "张嘉庆",
    "袁东": "袁东", "袁东老师": "袁东", "飞扬": "飞扬", "飞扬老师": "飞扬", "四海飞扬老师": "飞扬",
    "四海": "四海", "小马哥": "小马哥", "公考小马哥": "小马哥", "小张": "小张", "金标尺": "金标尺",
    "中指": "中指", "江牧云": "江牧云", "新途径": "新途径", "岭南贡院": "岭南贡院",
    "京佳教育": "京佳教育", "逸学公考": "逸学公考", "超格李崇立": "超格李崇立",
    "超格李崇立老师": "超格李崇立", "超格冰哥": "超格冰哥", "超格 — 冰哥": "超格冰哥",
    "朱老师": "朱老师", "22 老师": "22 老师", "高分学员": "高分学员",
    "公考沈建春老师": "公考沈建春老师", "小红薯公考静姐老师": "小红薯公考静姐老师",
    "一眉巫师": "一眉巫师", "相丽君": "相丽君", "上岸村": "上岸村", "公道公考": "公道公考",
    "申良言": "申良言", "导氮教育": "导氮教育", "Ace 超凡申论": "Ace 超凡申论",
    "淘宝试卷解析": "淘宝试卷解析", "公考某老师": "公考某老师",
    "远志公考思维课堂": "远志公考思维课堂", "高资 up": "高资 up", "张安威老师": "张安威",
    "师心不自用": "师心不自用", "申言说": "申言说", "唐棣讲申论": "唐棣讲申论",
    "青云端公考徐阳老师": "青云端公考徐阳老师", "高分网友回忆版": "高分网友回忆版",
    "刘司柠": "刘司柠", "王鹍": "王鹍", "千寻": "千寻", "独立申论": "独立申论",
    "OK 公考": "OK 公考", "申论大湿": "申论大湿", "半月谈 —— 老师": "半月谈", "申论": None,
}

def canon(c):
    c = c.strip()
    if c in WHITELIST:
        return WHITELIST[c]
    for k, v in WHITELIST.items():
        if v and (c == k or c.startswith(k)):
            return v
    return None

rows = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if not d.startswith('.')]
    for fn in sorted(filenames):
        if not fn.lower().endswith(('.txt', '.md')) or fn.lower() == 'readme.md':
            continue
        p = os.path.join(dirpath, fn)
        txt = io.open(p, encoding='utf-8', errors='replace').read()
        labels = []
        for line in txt.splitlines():
            s = line.strip().lstrip('#').strip()
            if not s:
                continue
            cands = []
            m = BOLD.match(s)
            if m:
                cands.append(m.group('name'))
            if COLON.match(s):
                cands.append(s)
            mi = INLINE.match(s)
            if mi:
                cands.append(mi.group('name'))
            if PLAIN.match(s) and len(s) <= 14:
                cands.append(s)
            for cand in cands:
                v = canon(cand)
                if v and v not in labels:
                    labels.append(v)
        rows.append({
            'folder': os.path.relpath(dirpath, ROOT).replace('\\', '/'),
            'file': os.path.splitext(fn)[0],
            'chars': len(re.sub(r'\s', '', txt)),
            'labels': labels,
        })

rows.sort(key=lambda r: (r['folder'], r['file']))
out = io.open(r"D:\Projtcts\shenlun\批改报告\.字数核验\ref-scan.txt", 'w', encoding='utf-8')
cur = None
for r in rows:
    if r['folder'] != cur:
        cur = r['folder']
        n = sum(1 for x in rows if x['folder'] == cur)
        out.write("\n### %s ｜ %d 题\n" % (cur, n))
    out.write("- %s ｜ %d 字 ｜ %s\n" % (
        r['file'], r['chars'], ("、".join(r['labels']) if r['labels'] else "（未识别到机构名标签）")))
out.write("\n合计文件数: %d\n" % len(rows))
out.close()
print("done", len(rows))
