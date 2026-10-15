'use strict';

const assert = require('node:assert/strict');
const { test, after } = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');

const repo = path.resolve(__dirname, '..');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'shenlun-install-'));
const skillRel = path.join('.dsh', 'skills', 'shenlun-judge');
const codexRel = path.join('.agents', 'skills', 'shenlun-judge');
let sequence = 0;

function workspace(label) {
  return path.join(scratch, `${++sequence}-${label} 中文 空格`);
}

function install(target, args = [], source = repo) {
  return spawnSync(process.execPath,
    [path.join(source, 'bin', 'install.js'), '--dir', target, ...args],
    { encoding: 'utf8', cwd: scratch });
}

function succeeds(result) {
  assert.equal(result.status, 0, result.stderr + result.stdout);
}

function entry(root, relative) {
  return fs.readFileSync(path.join(root, relative, 'SKILL.md'), 'utf8');
}

after(() => {
  // 只清理本测试创建的、已核实位于系统临时目录内的测试根目录。
  const resolved = fs.realpathSync(scratch);
  assert.equal(path.dirname(resolved), fs.realpathSync(os.tmpdir()));
  assert.ok(path.basename(resolved).startsWith('shenlun-install-'));
  fs.rmSync(resolved, { recursive: true, force: true });
});

test('default install gives Codex and dsh identical complete skills and shared tools', () => {
  const target = workspace('both');
  succeeds(install(target));
  assert.equal(entry(target, codexRel), entry(repo, skillRel));
  assert.equal(entry(target, skillRel), entry(repo, skillRel));
  for (const relative of [codexRel, skillRel]) {
    for (const file of ['references/rulings.md', 'references/runtime-compatibility.md', 'agents/openai.yaml']) {
      assert.equal(fs.readFileSync(path.join(target, relative, file), 'utf8'),
        fs.readFileSync(path.join(repo, skillRel, file), 'utf8'));
    }
  }
  assert.ok(fs.existsSync(path.join(target, '工具', '卷面核验.py')));
  assert.ok(fs.existsSync(path.join(target, '工具', 'ref_independence.py')));
  assert.ok(fs.existsSync(path.join(target, '批改报告', 'README.md')));
  assert.ok(!fs.existsSync(path.join(target, 'AGENTS.md')));
});

for (const harness of ['codex', 'dsh']) {
  test(`${harness} standalone installs only the selected harness with all references`, () => {
    const target = workspace(harness);
    succeeds(install(target, [`--harness=${harness}`]));
    const chosen = harness === 'codex' ? codexRel : skillRel;
    const other = harness === 'codex' ? skillRel : codexRel;
    assert.equal(entry(target, chosen), entry(repo, skillRel));
    assert.ok(!fs.existsSync(path.join(target, other)));
    for (const file of fs.readdirSync(path.join(repo, skillRel, 'references'))) {
      assert.ok(fs.existsSync(path.join(target, chosen, 'references', file)), file);
    }
  });
}

test('dry-run does not create a target or mutate an existing installation, even with force', () => {
  const target = workspace('dry');
  succeeds(install(target, ['--dry-run', '--force']));
  assert.ok(!fs.existsSync(target));
  succeeds(install(target, ['--harness', 'dsh']));
  const before = entry(target, skillRel);
  succeeds(install(target, ['--dry-run', '--force']));
  assert.equal(entry(target, skillRel), before);
  assert.ok(!fs.existsSync(path.join(target, codexRel)));
});

test('no-workspace still installs executable helpers', () => {
  const target = workspace('tools');
  succeeds(install(target, ['--harness', 'codex', '--no-workspace']));
  assert.ok(fs.existsSync(path.join(target, '工具', 'count_chars.py')));
  assert.ok(!fs.existsSync(path.join(target, '作答记录')));
});

test('updates preserve personal materials, directory registration, AGENTS.md and unrelated tools', () => {
  const target = workspace('update');
  succeeds(install(target));
  const preserved = ['作答记录/我的作答.txt', '批改报告/README.md', '参考答案/来源.md',
    '批改规范/自定.md', '工具/custom.py', 'AGENTS.md'];
  for (const file of preserved) fs.writeFileSync(path.join(target, file), '用户内容：' + file);
  for (const relative of [skillRel, codexRel]) {
    fs.writeFileSync(path.join(target, relative, 'stale.txt'), 'old');
    fs.writeFileSync(path.join(target, relative, 'SKILL.md'), 'outdated');
  }
  succeeds(install(target));
  assert.ok(fs.existsSync(path.join(target, codexRel, 'stale.txt')));
  succeeds(install(target, ['--force']));
  for (const relative of [skillRel, codexRel]) {
    assert.equal(entry(target, relative), entry(repo, skillRel));
    assert.ok(!fs.existsSync(path.join(target, relative, 'stale.txt')));
  }
  for (const file of preserved) {
    assert.equal(fs.readFileSync(path.join(target, file), 'utf8'), '用户内容：' + file);
  }
});

test('invalid harness or missing argument fails before creating files', () => {
  for (const args of [['--harness', 'unknown'], ['--harness'], ['--harness=']]) {
    const target = workspace('invalid');
    assert.equal(install(target, args).status, 2);
    assert.ok(!fs.existsSync(target));
  }
});

test('installing into the source repository with force preserves source and creates a forwarding entry', () => {
  const source = workspace('source');
  for (const relative of ['bin', skillRel, 'templates', '工具']) {
    fs.cpSync(path.join(repo, relative), path.join(source, relative), { recursive: true });
  }
  fs.copyFileSync(path.join(repo, 'package.json'), path.join(source, 'package.json'));
  const original = entry(source, skillRel);
  const tool = fs.readFileSync(path.join(source, '工具', 'count_chars.py'), 'utf8');
  succeeds(install(source, ['--force', '--no-workspace'], source));
  assert.equal(entry(source, skillRel), original);
  assert.equal(fs.readFileSync(path.join(source, '工具', 'count_chars.py'), 'utf8'), tool);
  const wrapper = entry(source, codexRel);
  const destination = wrapper.match(/\[共用 SKILL.md\]\(([^)]+)\)/)[1];
  assert.equal(fs.readFileSync(path.resolve(source, codexRel, destination), 'utf8'), original);
  assert.ok(fs.existsSync(path.join(source, codexRel, 'agents', 'openai.yaml')));
  succeeds(install(source, ['--force', '--no-workspace'], source));
  assert.equal(entry(source, skillRel), original);
});

test('directory junctions cannot redirect a force install outside the target workspace', (t) => {
  const target = workspace('junction');
  const outside = workspace('outside');
  fs.mkdirSync(target, { recursive: true });
  fs.mkdirSync(outside, { recursive: true });
  fs.writeFileSync(path.join(outside, 'keep.txt'), 'keep');
  try {
    fs.symlinkSync(outside, path.join(target, '.agents'), process.platform === 'win32' ? 'junction' : 'dir');
  } catch (error) {
    if (error.code === 'EPERM' || error.code === 'EACCES') {
      t.skip('This environment does not permit creating directory links.');
      return;
    }
    throw error;
  }
  const result = install(target, ['--force']);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.equal(fs.readFileSync(path.join(outside, 'keep.txt'), 'utf8'), 'keep');
  assert.ok(!fs.existsSync(path.join(outside, 'skills')));
  assert.ok(!fs.existsSync(path.join(target, skillRel)));
});

// 可选集成检查：指定本机已安装的 dsh-skill-filesystem/lib/index.js，零网络、不开模型会话。
test('installed dsh provider discovers and loads the shared skill', {
  skip: !process.env.DSH_SKILL_PROVIDER,
}, async () => {
  const { FileSystemSkillProvider } = await import(pathToFileURL(process.env.DSH_SKILL_PROVIDER).href);
  const warnings = [];
  const lifecycle = new AbortController();
  const provider = new FileSystemSkillProvider({
    get: () => undefined,
    logger: { warn: message => warnings.push(message) },
  }, { signal: lifecycle.signal, invalidate: () => {} }, {
    includeDefaultRoots: false,
    customSkillDirs: [path.join(repo, '.dsh', 'skills')],
    watch: false,
  });
  try {
    const observation = await provider.list({ cwd: repo });
    const candidates = Array.isArray(observation) ? observation : observation.candidates;
    const candidate = candidates.find(item => item.name === 'shenlun-judge');
    assert.ok(candidate, warnings.join('\n'));
    const loaded = await provider.get(candidate, { signal: lifecycle.signal });
    assert.equal(loaded.name, 'shenlun-judge');
    assert.ok(loaded.description.length > 0);
    assert.equal(loaded.resourceBase.path, path.join(repo, skillRel));
    const raw = entry(repo, skillRel).replace(/\r\n/g, '\n');
    assert.equal(loaded.content.replace(/\r\n/g, '\n'), raw.replace(/^---\n[\s\S]*?\n---/, '').trim());
    assert.deepEqual(warnings, []);
  } finally {
    lifecycle.abort();
    await provider.dispose();
  }
});
