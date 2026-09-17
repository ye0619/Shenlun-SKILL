#!/usr/bin/env node
/**
 * shenlun-skill 安装器（零依赖，仅用 Node 标准库）
 *
 *   npx shenlun-skill                     # 装到当前工作目录
 *   npx shenlun-skill --dir D:\my-ws      # 装到指定工作区
 *   npx shenlun-skill --dry-run           # 只打印将要做的事，不写盘
 *
 * 做的事：
 *   1. 把包内 .dsh/skills/shenlun-judge/ 复制到 <目标>/.dsh/skills/shenlun-judge/
 *   2. 在 <目标> 下补齐工作区脚手架目录（只在不存在时创建，不动用户已有内容）
 *
 * 退出码：0 成功；1 运行失败；2 用法错误。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const PKG_ROOT = path.resolve(__dirname, '..');
const SKILL_REL = path.join('.dsh', 'skills', 'shenlun-judge');
const TEMPLATE_REL = path.join('templates', 'workspace');
const SCAFFOLD_DIRS = ['参考答案', '作答记录', '批改报告', '批改规范', '真题'];

// 复制时跳过的文件/目录：缓存与系统垃圾，不进用户工作区
const SKIP_DIRS = new Set(['__pycache__', 'node_modules', '.git', '.miktex']);
const SKIP_FILES = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini']);

const USAGE = `shenlun-skill —— 申论阅卷人式批改训练 skill 安装器

用法：
  npx shenlun-skill [选项]

选项：
  --dir <路径>     安装到指定工作区目录（默认：当前目录）
  --force          清空并重建已存在的 skill 目录（删除旧版本残留文件）
  --dry-run        只打印将要执行的操作，不写盘
  --no-workspace   不创建工作区脚手架目录（参考答案/ 作答记录/ 批改报告/ 批改规范/ 真题/）
  -h, --help       显示本帮助
  --version        显示版本号

安装内容：
  <工作区>/.dsh/skills/shenlun-judge/   skill 本体（SKILL.md + references/ + scripts/）
  <工作区>/{参考答案,作答记录,批改报告,批改规范,真题}/README.md   目录使用说明（已存在则不覆盖）

装上以后，在 DeepSeek Harness 里打开该工作区，直接说「批改 2022河南乡镇」即可。`;

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
  const opts = { dir: '.', force: false, dryRun: false, workspace: true, help: false, version: false };
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

  // ---- 1. skill 本体 ----
  const skillDest = path.join(target, SKILL_REL);
  const skillExisted = fs.existsSync(path.join(skillDest, 'SKILL.md'));
  process.stdout.write(
    (skillExisted ? '[!] ' : '[+] ') +
      (skillExisted ? '检测到该工作区已装过本 skill，本次为更新安装：旧版本文件会被覆盖' +
        (opts.force ? '（--force：先清空旧目录再重建）' : '（如需清掉旧版本残留文件，请加 --force）') + '\n'
        : '将安装 skill 到 ' + skillDest + '\n')
  );
  if (skillExisted) {
    process.stdout.write('    目标：' + skillDest + '\n');
  }
  process.stdout.write('    ' + tag('复制 ' + skillFiles.length + ' 个文件：SKILL.md、references/、scripts/count_chars.py\n'));

  if (!dryRun) {
    try {
      if (skillExisted && opts.force) {
        fs.rmSync(skillDest, { recursive: true, force: true });
      }
      fs.mkdirSync(path.dirname(skillDest), { recursive: true });
      fs.cpSync(skillSrc, skillDest, { recursive: true, force: true, filter: copyFilter });
      process.stdout.write((skillExisted ? '[=] ' : '[+] ') + (skillExisted ? 'skill 已更新：' : 'skill 已安装：') + skillDest + '\n');
    } catch (err) {
      fail('复制 skill 失败：' + err.message + '\n    源：' + skillSrc + '\n    目标：' + skillDest);
    }
  } else {
    process.stdout.write(tag('    → ' + skillDest + '（含 ' + skillFiles.length + ' 个文件）\n'));
  }

  // ---- 2. 工作区脚手架目录 ----
  if (!opts.workspace) {
    process.stdout.write('\n[i] --no-workspace：跳过工作区脚手架目录\n');
  } else {
    process.stdout.write('\n工作区脚手架目录（已存在的内容一律不覆盖）：\n');
    for (const dirName of SCAFFOLD_DIRS) {
      const dirPath = path.join(target, dirName);
      const tplReadme = path.join(PKG_ROOT, TEMPLATE_REL, dirName, 'README.md');
      const destReadme = path.join(dirPath, 'README.md');
      const dirExisted = fs.existsSync(dirPath);
      const readmeExisted = fs.existsSync(destReadme);

      if (!dirExisted && !dryRun) {
        fs.mkdirSync(dirPath, { recursive: true });
      }
      if (dryRun) {
        process.stdout.write(tag('    ' + dirName + '/  目录' + (dirExisted ? '已存在（保留）' : '将创建')));
      } else {
        process.stdout.write('    ' + dirName + '/  ' + (dirExisted ? '目录已存在（保留）' : '已创建'));
      }

      if (readmeExisted) {
        process.stdout.write('，README.md 已存在，跳过（不覆盖）\n');
        continue;
      }
      if (!fs.existsSync(tplReadme)) {
        process.stdout.write('，包内缺少模板 README.md，跳过\n');
        continue;
      }
      if (dryRun) {
        process.stdout.write('，将写入 README.md\n');
      } else {
        try {
          fs.copyFileSync(tplReadme, destReadme);
          process.stdout.write('，已写入 README.md\n');
        } catch (err) {
          fail('写入 ' + destReadme + ' 失败：' + err.message);
        }
      }
    }
  }

  // ---- 3. 下一步提示 ----
  process.stdout.write('\n' + (dryRun ? '以上为演练结果，未写入任何文件。去掉 --dry-run 即真正安装。\n' : '装好了。\n'));
  process.stdout.write(
    '下一步：\n' +
      '  1. 在 DeepSeek Harness 里打开工作区：' + target + '\n' +
      '  2. 直接说：批改 2022河南乡镇   （或：批改 .\\作答记录\\河南\\2022河南乡镇.txt）\n' +
      '  3. 真题卷、参考答案、你的作答丢到工作区根目录或对应目录即可，助手会按规范自动归位\n' +
      '  4. skill 说明：' + path.join(skillDest, 'README.md') + '\n' +
      '  5. 更新或重装：npx shenlun-skill --force\n'
  );
  return 0;
}

try {
  process.exitCode = main();
} catch (err) {
  fail('安装失败：' + (err && err.message ? err.message : String(err)));
}
