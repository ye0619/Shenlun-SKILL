import io, re

p = r"D:\Projtcts\shenlun\README.md"
lines = io.open(p, encoding='utf-8').read().split('\n')

start = next(i for i, l in enumerate(lines) if l.startswith('## 2026年国考《申论》（行政执法卷）批改报告'))
end = next(i for i, l in enumerate(lines) if l.startswith('## 五、这套 skill 的不足'))

in_fence = False
changed = 0
for i in range(start, end):
    l = lines[i]
    if l.lstrip().startswith('```'):
        in_fence = not in_fence
        continue
    if in_fence:
        if l.startswith('## 第2/3/4题同法'):   # 代码块内被误降级的注释行，还原
            lines[i] = l[1:]
            changed += 1
        continue
    if re.match(r'^#{1,5}\s', l):
        lines[i] = '#' + l
        changed += 1

io.open(p, 'w', encoding='utf-8').write('\n'.join(lines))
print('demoted lines:', changed, '| range', start, end)
