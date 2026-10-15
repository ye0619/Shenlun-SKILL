# shenlun-judge — 申论阅卷 Skill

申论模拟批改、作答指导与素材整理。完整评分原则和唯一执行流程见 [SKILL.md](SKILL.md)；用户硬口径以 `references/rulings.md` 为唯一事实源。本文只说明安装和文件入口，不复制评分细则，避免规则分叉。

## 安装

```bash
npx github:ye0619/Shenlun-SKILL
npx shenlun-skill
node bin/install.js --dir <目标工作区>
node bin/install.js --harness codex
```

安装器按 `--harness codex|dsh|all` 安装 skill，并可将配套脚本放入工作区 `工具/`、创建真题、参考答案、作答记录、批改报告和批改规范目录。常用参数：`--force`、`--dry-run`、`--no-workspace`、`--help`。源码仓库自安装时，Codex 使用转发入口与 DSH 共用正文。

评分依据、证据要求、模拟评分边界、各题型流程、报告结构和维护约束均以 `SKILL.md` 及其明确引用的专题文件为准。当前没有人工核定的阅卷标签或标准分，报告不得声称准确率已验证或评分已校准；参考材料和模型自查不能替代人工基准。

工作区路径和运行环境约定见 `references/workspace-layout.md` 与 `references/runtime-compatibility.md`。仓库：[GitHub](https://github.com/ye0619/Shenlun-SKILL)。
