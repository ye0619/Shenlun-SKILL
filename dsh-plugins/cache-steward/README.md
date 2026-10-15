# @local/dsh-cache-steward（缓存管家）

Harness 侧的提示词缓存管家。目标是**提高 API 缓存命中率、减少 token 消耗**。

## 为什么要它

DeepSeek/Anthropic 风格的提示词缓存按**精确 token 前缀**匹配：只要某次请求从某个位置开始和上一次不同，
该位置之后的全部前缀都要**重新计费**。实测（本机代理日志 2026-09-30 → 2026-10-09，207 次请求）：
整体命中率已有 **90.4%**（16,317.5K 输入 / 14,755.6K 命中），但漏掉的 1,561.9K token 高度集中：

| 漏计费来源 | token | 说明 |
| --- | --- | --- |
| 每会话首个请求（冷启动） | 877.5K | 13 个会话，无法避免 |
| 折叠（fold）重付 | 273.2K | **仅 6 次折叠**，单次最高 ≈367K |
| 其他（尾部增长/TTL/中断重发） | 411.2K | 其中 30.5% 落在客户端中断后 30s 内 |

也就是说：**命中率不是被"慢慢磨掉"的，而是被少数超大事件一次性打断的**——一次 262K token 的会话起点、
一次在很深处发生的折叠，都会让整个前缀重付一遍。

本插件负责两件 Harness 侧确实能控制的事：

1. **诊断**：在 `llm/stream` 上逐请求做前缀指纹比对，定位**第一个发生变化的段**（是系统提示、工具表、
   历史消息还是别的），并估算因此重付的字节数。
2. **截断**：`tools/post-execute` 里对超过预算的工具结果做"保头保尾"裁剪，避免超大结果进入会话日志。
   工具结果一旦进入日志，之后**每一次**请求都要重付它，还会抬高折叠的代价。

## 安装

使用 Host 的 `plugin_manager` 工具，action=`install_bundle`，target=`D:\Projects\shenlun\dsh-plugins\cache-steward`。
安装会请求一次 danger-full-access 审批（插件是 Host 代码，会写入 profile）。

## 配置（`cordis.patch.yml` 或 Plugin Manager）

| 键 | 默认 | 含义 |
| --- | --- | --- |
| `trimOversizedToolResults` | `true` | 是否裁剪超大工具结果 |
| `toolResultSoftCapBytes` | `24576` | 单个工具结果的字节预算，超出则保头(60%)保尾(40%)并插入省略说明 |
| `reportTopEvents` | `8` | 报告里列出的最重片段/工具数量 |
| `logSeams` | `true` | 每检测到一次缓存断裂就写一条信息级日志 |

## 工具

插件注册一个只读工具 `cache_steward`：

- `action: "report"` — 返回本会话的请求数、检测到的缓存断裂次数与位置、估算重付字节、
  当前请求最重的若干段、本会话最重的若干工具输出。
- `action: "reset"` — 清空内存计数器。

## 边界

- **命中率本身在 provider/代理层计量**（本机由 billion-context 代理放置 `cache_control`），
  插件不修改缓存标记，只解释"哪次 Harness 侧改动造成了断裂"。
- 指纹只保留 `h(内容):字节数`，不保留任何提示词正文。
- 折叠（compress）造成的重付由上游压缩机制决定，插件不介入，只在报告中体现。
- 若 `llm/stream` 的载荷结构变化，插件会退化为"记录警告"而不是让请求失败。

## files

`index.js`、`cordis.patch.yml`、`icon.svg`、`locale/*.json`、`README.md`
