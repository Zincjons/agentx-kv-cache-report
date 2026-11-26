# AgentX / Sage 理论前缀缓存实验

用 Python 标准库计算 InferenceX `Cached input`，比较 Sage 的规则 session、用户缓存域和直接 cache-aware 重放。需要 **Python 3.10+**，无需安装第三方依赖。

## 四个真实数据集复现 example

先按下文准备 `data/` 中的真实数据，再在仓库根目录运行。默认入口不使用合成数据：

```bash
python3 examples/agentx.py
python3 examples/sage_session_id.py
python3 examples/sage_user_id.py
python3 examples/sage_cache_aware.py
```

也可以一次运行四项，并将实际计算出的请求数、分子、分母与参考结果逐整数核对：

```bash
python3 examples/run_all.py
```

| Example | 默认真实数据范围 | 预期理论命中率 |
|---|---|---:|
| `examples/agentx.py` | AgentX 全量，393 会话、98,827 次调用 | **98.015600%** |
| `examples/sage_session_id.py` | Sage 11-16～18 三天全量 trace，按规则 session-id 隔离 | **61.091304%** |
| `examples/sage_user_id.py` | Sage 11-16～18 可靠公共请求，按 `(uid,model)` 跨天保留历史 | **74.123859%** |
| `examples/sage_cache_aware.py` | Sage 11-16～18 三天全量 schedule，4 副本各 50 万块，跨天保留历史 | **74.077481%** |

三个 Sage example 默认都使用 **2026-11-16～18 三天全量**；用户及 cache-aware 主结果均跨天保留历史。若每天清空缓存，三天按输入 token 加权结果分别为 **74.029399%**、**74.010221%**，输出中会并列保存。

cache-aware 同时输出共享无限、共享 LRU、轮询和随机对照，并明确主结果为 `replicas-cache-aware`。规则 session 使用 211,603 条 trace 请求；用户实验使用可可靠关联 uid 的 211,538 条公共请求；直接 cache-aware 使用 211,596 条 schedule 请求。这些是各数据表示的完整可用范围，分母明确记录。

默认每次在 `results/examples/` 新建输出目录。真实数据缺失时会直接提示准备数据，不会回退到演示输入。实际执行、分子分母和日期范围见 [examples/README.md](examples/README.md)。

## 计分口径

从当前输入的 `hash_ids` 开头数连续已在对应历史中的块，遇到第一个未见块停止：

```text
命中 token = min(输入 token, 连续命中块数 × block_size)
整体命中率 = 命中输入 token 总和 / 输入 token 总和
```

计分后把本次全部输入块加入历史，输出 token 不加入。整体比例按输入 token 加权，不是逐请求百分比的算术平均。

- AgentX 每条真实会话从空主线历史开始。子 agent 从当时主线历史副本起步，独立累积，不回写主线。
- Sage 的 `trace.id` 是规则分组，不是真实任务标签；uid 来自其用户前缀。同用户可跨规则分组保留历史。
- `(uid,model)` 是缓存域，不表示同一个任务。不同任务的公共系统提示词等也可以产生有效命中。
- 直接 cache-aware 只读 schedule，按同模型共享或副本本地缓存。没有 schedule 中不存在的用户隔离信息。

除指定 LRU 容量外，历史无限保留。指标为离线理论复用比例，不模拟并发 KV 完成时间或吞吐。

## 全量数据

原始大文件、压缩包和逐请求输出由 `.gitignore` 排除。将已有数据放到以下位置，或通过参数指定路径：

```text
data/traces.jsonl
data/sage/2026-11-16/Sage-S2.1-flash/{trace,schedule,flat,stats}/
data/sage/2026-11-17/Sage-S2.1-flash/{trace,schedule,flat,stats}/
data/sage/2026-11-18/Sage-S2.1-flash/{trace,schedule,flat,stats}/
```

AgentX 全量 SHA-256：`29b6a19e751ff5230771519aab755f80a0f43a4ba9cf96b72d3a6a437ec99276`；固定数据版本 `23f152f6f0f9399a85901b89a6458def0ef16729`。

```bash
python3 examples/agentx.py --input data/traces.jsonl --limit 393 --output-dir results/agentx-full

python3 examples/sage_session_id.py --data-root data/sage --days 2026-11-16 2026-11-17 2026-11-18 --output-dir results/sage-session-full

python3 examples/sage_user_id.py --data-root data/sage --days 2026-11-16 2026-11-17 2026-11-18 --output-dir results/common

python3 examples/sage_cache_aware.py --data-root data/sage --days 2026-11-16 2026-11-17 2026-11-18 --capacity-blocks 500000 --replicas 4 --output-dir results/cache-aware-full
```

指定的输出目录必须不存在。

## 本地实验入口

| 模块 | 功能 |
|---|---|
| `analyze_agentx.py` | 原始 AgentX 算法及逐请求 CSV，保持未修改 |
| `experiments.schedule` | 只读 schedule，单共享无限历史，逐日及跨天统计 |
| `experiments.trace_users` | trace 按用户累计，对照规则分组，用 schedule 核验顺序 |
| `experiments.common` | 公共请求严格匹配、时间消歧、用户/全局缓存对照 |
| `experiments.cache_aware` | 共享/LRU/轮询/随机/缓存前缀路由 |
| `experiments.request_chains` | 长前缀＋完成时间＋tool_use 初步关联、阈值及时间打乱对照 |

```bash
python3 -m experiments.schedule --data-root data/sage --output-dir results/schedule
python3 -m experiments.trace_users --data-root data/sage --output-dir results/trace-users
python3 -m experiments.common --data-root data/sage --output-dir results/common-other
python3 -m experiments.request_chains --common-dir results/common --output-dir results/request-chains
```

请求关联产生候选组，不是真实任务标签。未知请求冷启动的场景值不是已验证的“真实任务内命中率”。已核对结果和局限见 [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)。

## 测试

仅想快速检查代码而没有真实数据时，必须显式选择演示模式；这些数值不是正式数据集结果：

```bash
python3 examples/run_all.py --demo
# 或逐项：python3 examples/agentx.py --demo 等
```

```bash
python3 -B -m unittest discover -s tests -v
```

覆盖真实数据默认配置、缺数据时不回退 demo、显式演示模式、四个 example、公共请求到关联分析的 CLI 流程，以及分支快照、用户/模型隔离、重复消歧、LRU 和路由约束。

## 原始参考

- [AgentX 固定版本](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/tree/23f152f6f0f9399a85901b89a6458def0ef16729)
- [InferenceX 前缀与分支算法](https://github.com/SemiAnalysisAI/InferenceX-app/blob/1a191f6e242e54007551317fc26a2129a5726f21/packages/db/src/etl/weka-structure.ts#L100)
- [InferenceX 汇总公式](https://github.com/SemiAnalysisAI/InferenceX-app/blob/1a191f6e242e54007551317fc26a2129a5726f21/packages/db/src/ingest-weka-dataset.ts#L232)
- [原始 AgentX 实验报告](EXPERIMENT_REPORT.md)
