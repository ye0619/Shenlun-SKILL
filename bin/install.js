#!/usr/bin/env node
/**
 * shenlun-skill 安装器（零依赖，仅用 Node 标准库）
 *
 *   npx shenlun-skill                     # 装到当前工作目录
 *   npx shenlun-skill --dir D:\my-ws      # 装到指定工作区
 *   npx shenlun-skill --dry-run           # 只打印将要做的事，不写盘
 *
 * 做的事：
 *   1. 从同一份 skill 源码安装到 Codex / dsh 的发现目录
 *   2. 在 <目标> 下补齐工作区脚手架目录（只在不存在时创建，不动用户已有内容）
 *
 * 退出码：0 成功；1 运行失败；2 用法错误。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const PKG_ROOT = path.resolve(__dirname, '..');
const SKILL_REL = path.join('.dsh', 'skills', 'shenlun-judge');
const HARNESS_DIRS = {
  codex: path.join('.agents', 'skills', 'shenlun-judge'),
  dsh: SKILL_REL,
};
const TEMPLATE_REL = path.join('templates', 'workspace');
/** skill 要调用的配套脚本：放在工作区根目录的工具目录里（skill 文档按此路径调用） */
const TOOLS_REL = '工具';
const SCAFFOLD_DIRS = ['参考答案', '作答记录', '批改报告', '批改规范', '真题'];

// 复制时跳过的文件/目录：缓存与系统垃圾，不进用户工作区
const SKIP_DIRS = new Set(['__pycache__', 'node_modules', '.git', '.miktex']);
const SKIP_FILES = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini']);

const USAGE = `shenlun-skill —— 申论阅卷人式批改训练 skill 安装器

用法：
  npx shenlun-skill [选项]

选项：
  --dir <路径>     安装到指定工作区目录（默认：当前目录）
  --harness <名称> codex | dsh | all（默认 all，同时支持 Codex 和 dsh）
  --force          清空并重建已存在的 skill 目录（删除旧版本残留文件）
  --dry-run        只打印将要执行的操作，不写盘
  --no-workspace   不创建五个材料目录（参考答案/ 作答记录/ 批改报告/ 批改规范/ 真题/）；
                   工具/ 仍会安装，因为 skill 要调用它
  -h, --help       显示本帮助
  --version        显示版本号

安装内容：
  <工作区>/.agents/skills/shenlun-judge/  Codex skill（--harness codex / all）
  <工作区>/.dsh/skills/shenlun-judge/     dsh skill（--harness dsh / all）
  <工作区>/工具/                        skill 的配套脚本（count_chars.py 字数核验、ref_independence.py 同源检测）
  <工作区>/{参考答案,作答记录,批改报告,批改规范,真题}/README.md   目录使用说明（已存在则不覆盖）

装上以后，在对应 harness 里打开工作区，直接说「批改 2022河南乡镇」即可。
Codex 也可显式调用：$shenlun-judge。安装到本源码仓库时，Codex 使用转发入口，避免维护两份规则。`;

function fail(message, code) {
  process.stderr.write('[x] ' + message + '\n');
  process.exit(code === undefined ? 1 : code);
}

function usageFail(message) {
  process.stderr.write('[x] ' + message + '\n\n' + USAGE + '\n');
  process.exit(2);
}

function pkgVersion() {
  try {
    return JSON.parse(fs.readFileSync(path.join(PKG_ROOT, 'package.json'), 'utf8')).version || '0.0.0';
  } catch (err) {
    return '0.0.0';
  }
}

function parseArgs(argv) {
  const opts = { dir: '.', harness: 'all', force: false, dryRun: false, workspace: true, help: false, version: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      opts.help = true;
    } else if (arg === '--version') {
      opts.version = true;
    } else if (arg === '--force') {
      opts.force = true;
    } else if (arg === '--dry-run') {
      opts.dryRun = true;
    } else if (arg === '--no-workspace') {
      opts.workspace = false;
    } else if (arg === '--harness' || arg.startsWith('--harness=')) {
      const value = arg === '--harness' ? argv[++i] : arg.slice('--harness='.length);
      if (!['codex', 'dsh', 'all'].includes(value)) {
        usageFail('--harness 必须是 codex、dsh 或 all');
      }
      opts.harness = value;
    } else if (arg === '--dir') {
      const value = argv[i + 1];
      if (value === undefined || value.startsWith('--')) {
        usageFail('--dir 后面缺少路径，例如：--dir "D:\\my-workspace"');
      }
      opts.dir = value;
      i++;
    } else if (arg.startsWith('--dir=')) {
      const value = arg.slice('--dir='.length);
      if (!value) usageFail('--dir= 后面缺少路径，例如：--dir="D:\\my-workspace"');
      opts.dir = value;
    } else {
      usageFail('未知参数：' + arg);
    }
  }
  return opts;
}

function shouldSkip(name) {
  if (SKIP_DIRS.has(name)) return true;
  if (SKIP_FILES.has(name)) return true;
  if (name.endsWith('.pyc') || name.endsWith('.pyo')) return true;
  return false;
}

function copyFilter(src) {
  return !shouldSkip(path.basename(src));
}

function sameDirectory(a, b) {
  if (!fs.existsSync(a) || !fs.existsSync(b)) return false;
  return fs.realpathSync(a) === fs.realpathSync(b);
}

function rejectOverlap(source, destination) {
  const src = fs.realpathSync(source);
  const dest = fs.existsSync(destination) ? fs.realpathSync(destination) : path.resolve(destination);
  if (src === dest) return;
  const nested = (a, b) => {
    const relative = path.relative(a, b);
    return relative && !relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative);
  };
  if (nested(src, dest) || nested(dest, src)) {
    fail('安装目标与源码相互包含，无法安全复制：' + destination);
  }
}

// 在写入或 --force 删除前检查真实路径；拒绝经符号链接/目录联接写到工作区外。
function checkedDestination(target, relative) {
  let current = target;
  for (const part of relative.split(path.sep)) {
    current = path.join(current, part);
    if (fs.existsSync(current) && fs.lstatSync(current).isSymbolicLink()) {
      fail('安装路径含符号链接或目录联接，请使用普通目录：' + current);
    }
  }
  const resolved = path.resolve(target, relative);
  const inside = path.relative(target, resolved);
  if (!inside || inside.startsWith('..' + path.sep) || path.isAbsolute(inside)) {
    fail('安装路径必须位于工作区内：' + resolved);
  }
  return resolved;
}

/** 递归列出目录下所有文件（相对路径，已过滤缓存垃圾） */
function listFiles(root, base) {
  const out = [];
  const walk = (dir, rel) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (shouldSkip(entry.name)) continue;
      const abs = path.join(dir, entry.name);
      const relPath = rel ? path.join(rel, entry.name) : entry.name;
      if (entry.isDirectory()) walk(abs, relPath);
      else if (entry.isFile()) out.push(relPath);
    }
  };
  walk(root, base || '');
  return out;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));

  if (opts.help) {
    process.stdout.write(USAGE + '\n');
    return 0;
  }
  if (opts.version) {
    process.stdout.write(pkgVersion() + '\n');
    return 0;
  }

  const dryRun = opts.dryRun;
  const tag = (text) => (dryRun ? '[dry-run] ' + text : text);

  // ---- 校验包内 payload ----
  const skillSrc = path.join(PKG_ROOT, SKILL_REL);
  const skillEntry = path.join(skillSrc, 'SKILL.md');
  if (!fs.existsSync(skillEntry)) {
    fail('安装包内容不完整：找不到 ' + skillEntry + '（skill 本体缺失，请重新安装本包）');
  }
  const skillFiles = listFiles(skillSrc);

  // ---- 解析并检查目标目录 ----
  const target = path.resolve(process.cwd(), opts.dir || '.');
  let targetExists = fs.existsSync(target);
  if (targetExists && !fs.statSync(target).isDirectory()) {
    fail('安装目标不是目录：' + target);
  }

  process.stdout.write('shenlun-skill v' + pkgVersion() + (dryRun ? '（演练模式，不写盘）' : '') + '\n');
  process.stdout.write('工作区目录：' + target + '\n\n');

  if (!targetExists && !dryRun) {
    fs.mkdirSync(target, { recursive: true });
    targetExists = true;
    process.stdout.write('[+] 目录不存在，已创建工作区目录\n');
  } else if (!targetExists && dryRun) {
    process.stdout.write(tag('[+] 将创建工作区目录（当前不存在）\n'));
  }

  // ---- 1. skill 本体：同一源文件分别安装，不改 AGENTS.md 或全局配置 ----
  const harnesses = opts.harness === 'all' ? ['codex', 'dsh'] : [opts.harness];
  // 在任何复制/清理前检查所有目标。
  for (const harness of harnesses) {
    rejectOverlap(skillSrc, checkedDestination(target, HARNESS_DIRS[harness]));
  }
  if (fs.existsSync(path.join(PKG_ROOT, TOOLS_REL))) {
    rejectOverlap(path.join(PKG_ROOT, TOOLS_REL), checkedDestination(target, TOOLS_REL));
  }
  for (const harness of harnesses) {
    const skillDest = checkedDestination(target, HARNESS_DIRS[harness]);
    if (sameDirectory(skillSrc, skillDest)) {
      process.stdout.write('[i] dsh 目标就是源码目录，保留源码（--force 也不删除）\n');
      continue;
    }
    // 直接使用源码仓库时只维护一个正文；发布安装则复制完整、自足的 skill。
    const repoEntry = harness === 'codex' && sameDirectory(target, PKG_ROOT);
    const copySrc = repoEntry ? path.join(PKG_ROOT, 'templates', 'codex') : skillSrc;
    const skillExisted = fs.existsSync(skillDest);
    process.stdout.write(
      (skillExisted ? '[!] ' : '[+] ') +
        (skillExisted ? '检测到该工作区已装过本 skill，本次为更新安装：旧版本文件会被覆盖' +
          (opts.force ? '（--force：先清空旧目录再重建）' : '（如需清掉旧版本残留文件，请加 --force）') + '\n'
          : '将安装 skill 到 ' + skillDest + '\n')
    );
    if (skillExisted) {
      process.stdout.write('    目标：' + skillDest + '\n');
    }
    process.stdout.write('    ' + tag(harness + '：' + (repoEntry ? '写入源码转发入口' : '复制 ' + skillFiles.length + ' 个 skill 文件') + '\n'));

    if (!dryRun) {
      try {
        if (skillExisted && opts.force) {
          fs.rmSync(skillDest, { recursive: true, force: true });
        }
        fs.mkdirSync(path.dirname(skillDest), { recursive: true });
        fs.cpSync(copySrc, skillDest, { recursive: true, force: true, filter: copyFilter });
        if (repoEntry) {
          fs.cpSync(path.join(skillSrc, 'agents'), path.join(skillDest, 'agents'),
            { recursive: true, force: true, filter: copyFilter });
        }
        process.stdout.write((skillExisted ? '[=] ' : '[+] ') + (skillExisted ? 'skill 已更新：' : 'skill 已安装：') + skillDest + '\n');
      } catch (err) {
        fail('复制 skill 失败：' + err.message + '\n    源：' + skillSrc + '\n    目标：' + skillDest);
      }
    } else {
      process.stdout.write(tag('    → ' + skillDest + '（含 ' + skillFiles.length + ' 个文件）\n'));
    }
  }

  // ---- 2. 配套脚本（工具/）----
  // skill 的文档按固定路径调用 <工作区>/工具/{count_chars.py,ref_independence.py}，
  // 因此这两个脚本必须落在工作区里；--no-workspace 也照装（否则 skill 无法核验字数/判同源）。
  const toolsSrc = path.join(PKG_ROOT, TOOLS_REL);
  const toolsDest = path.join(target, TOOLS_REL);
  if (!fs.existsSync(toolsSrc)) {
    process.stdout.write('\n[!] 包内缺少 ' + TOOLS_REL + '/，跳过配套脚本（skill 的字数核验与同源检测将不可用）\n');
  } else {
    const toolFiles = listFiles(toolsSrc);
    const toolsExisted = fs.existsSync(toolsDest);
    process.stdout.write('\n配套脚本（skill 调用，路径固定请勿改名）：\n');
    process.stdout.write(
      tag('    ' + TOOLS_REL + '/  ' + (toolsExisted ? '目录已存在（保留其他文件）' : '将创建') +
        '，写入 ' + toolFiles.length + ' 个文件：' + toolFiles.join('、') + '\n')
    );
    if (sameDirectory(toolsSrc, toolsDest)) {
      process.stdout.write('    工具目标就是源码目录，保留原文件\n');
    } else if (!dryRun) {
      try {
        fs.mkdirSync(toolsDest, { recursive: true });
        fs.cpSync(toolsSrc, toolsDest, { recursive: true, force: true, filter: copyFilter });
        process.stdout.write('    ' + TOOLS_REL + '/  ' + (toolsExisted ? '已更新：' : '已安装：') + toolsDest + '\n');
      } catch (err) {
        fail('复制配套脚本失败：' + err.message + '\n    源：' + toolsSrc + '\n    目标：' + toolsDest);
      }
    }
  }

  // ---- 3. 工作区脚手架目录 ----
  if (!opts.workspace) {
    process.stdout.write('\n[i] --no-workspace：跳过五个材料目录\n');
  } else {
    process.stdout.write('\n工作区脚手架目录（已存在的内容一律不覆盖）：\n');
    for (const dirName of SCAFFOLD_DIRS) {
      const dirPath = path.join(target, dirName);
      const tplDir = path.join(PKG_ROOT, TEMPLATE_REL, dirName);
      const dirExisted = fs.existsSync(dirPath);

      if (!dirExisted && !dryRun) {
        fs.mkdirSync(dirPath, { recursive: true });
      }
      if (dryRun) {
        process.stdout.write(tag('    ' + dirName + '/  目录' + (dirExisted ? '已存在（保留）' : '将创建')));
      } else {
        process.stdout.write('    ' + dirName + '/  ' + (dirExisted ? '目录已存在（保留）' : '已创建'));
      }

      // 目录内模板：README.md（使用说明）＋ `_*.md`（可复制使用的模板，如
      // `真题/_TEMPLATE.md`、`批改规范/_模板.md`、`批改规范/_动态评分规范模板.md`）。
      // 逐份判断"目标已存在则不覆盖"，与 README.md 同规矩。
      const tplFiles = fs.existsSync(tplDir)
        ? fs.readdirSync(tplDir).filter((f) => f === 'README.md' || (/^_.+\.md$/.test(f)))
        : [];
      if (!tplFiles.length) {
        process.stdout.write('，包内缺少模板文件，跳过\n');
        continue;
      }
      const written = [];
      for (const f of tplFiles) {
        const dest = path.join(dirPath, f);
        if (fs.existsSync(dest)) {
          written.push(f + '(已存在,跳过)');
          continue;
        }
        if (dryRun) {
          written.push(f + '(将写入)');
        } else {
          try {
            fs.copyFileSync(path.join(tplDir, f), dest);
            written.push(f);
          } catch (err) {
            fail('写入 ' + dest + ' 失败：' + err.message);
          }
        }
      }
      process.stdout.write('，模板：' + written.join('、') + '\n');
    }
  }

  // ---- 4. 下一步提示 ----
  process.stdout.write('\n' + (dryRun ? '以上为演练结果，未写入任何文件。去掉 --dry-run 即真正安装。\n' : '装好了。\n'));
  process.stdout.write(
    '下一步：\n' +
      '  1. 在 ' + harnesses.join(' / ') + ' 里打开工作区：' + target + '\n' +
      '  2. 直接说：批改 2022河南乡镇   （或：批改 .\\作答记录\\河南\\2022河南乡镇.txt）\n' +
      '  3. 真题卷、参考答案、你的作答丢到工作区根目录或对应目录即可，助手会按规范自动归位\n' +
      '  4. skill 入口：' + harnesses.map(h => path.join(target, HARNESS_DIRS[h], 'SKILL.md')).join('、') + '\n' +
      '  5. 配套脚本：' + toolsDest + '（' + TOOLS_REL + '/count_chars.py 字数核验、' + TOOLS_REL + '/ref_independence.py 参考答案同源检测）\n' +
      '  6. 更新或重装：npx shenlun-skill --harness ' + opts.harness + ' --force\n'
  );
  return 0;
}

try {
  process.exitCode = main();
} catch (err) {
  fail('安装失败：' + (err && err.message ? err.message : String(err)));
}
