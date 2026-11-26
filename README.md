# AgentX / Sage 理论前缀缓存实验

用 Python 标准库计算 InferenceX `Cached input`，比较 Sage 的规则 session、用户缓存域和直接 cache-aware 重放。需要 **Python 3.10+**，无需安装第三方依赖。

## 四个可运行的 example

在仓库根目录运行：

```bash
python3 examples/agentx.py
python3 examples/sage_session_id.py
python3 examples/sage_user_id.py
python3 examples/sage_cache_aware.py
```

默认使用仓库内手工合成的小数据，每次在 `results/examples/` 新建输出目录。完整解释、逐步计算与全量命令见 [examples/README.md](examples/README.md)。

| Example | 历史范围 | 合成样例预期 |
|---|---|---:|
| `examples/agentx.py` | AgentX 会话隔离、子 agent 继承主线快照 | 384 / 960 = **40.0000%** |
| `examples/sage_session_id.py` | Sage 每个规则 session-id 独立历史 | 256 / 896 = **28.5714%** |
| `examples/sage_user_id.py` | trace/schedule 可靠公共请求，按 `(uid,model)` 累计 | 448 / 896 = **50.0000%** |
| `examples/sage_cache_aware.py` | 直接 schedule，共享缓存、LRU、多副本路由 | 共享 **64.2857%**；轮询 **42.8571%**；前缀路由 **64.2857%** |

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

python3 examples/sage_session_id.py --trace-dir data/sage/2026-11-18/Sage-S2.1-flash/trace --output-dir results/sage-session-18

python3 examples/sage_user_id.py --data-root data/sage --days 2026-11-16 2026-11-17 2026-11-18 --output-dir results/common

python3 examples/sage_cache_aware.py --day-dir data/sage/2026-11-18/Sage-S2.1-flash --capacity-blocks 500000 --replicas 4 --output-dir results/cache-aware-18
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

```bash
python3 -B -m unittest discover -s tests -v
```

覆盖四个 example、公共请求到关联分析的 CLI 流程、分支快照、用户/模型隔离、重复消歧、LRU 首个未见块、冷缓存路由平局以及关联的时间和完整块约束。

## 原始参考

- [AgentX 固定版本](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/tree/23f152f6f0f9399a85901b89a6458def0ef16729)
- [InferenceX 前缀与分支算法](https://github.com/SemiAnalysisAI/InferenceX-app/blob/1a191f6e242e54007551317fc26a2129a5726f21/packages/db/src/etl/weka-structure.ts#L100)
- [InferenceX 汇总公式](https://github.com/SemiAnalysisAI/InferenceX-app/blob/1a191f6e242e54007551317fc26a2129a5726f21/packages/db/src/ingest-weka-dataset.ts#L232)
- [原始 AgentX 实验报告](EXPERIMENT_REPORT.md)
