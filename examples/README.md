# 四个真实数据集复现实验

默认分析 AgentX 全量及 Sage **2026-11-16～18 三天全量**；用户和 cache-aware 实验的主结果均跨天保留历史。缺少数据时明确报错，不会自动换成合成样例。

先按根目录 README 准备 `data/traces.jsonl` 和 `data/sage/<日期>/Sage-S2.1-flash/`。所有命令在仓库根目录运行。

## 一次运行全部真实实验

```bash
python3 examples/run_all.py
```

依次执行四个 example，将请求数、命中 token、输入 token 与 `reports/reference_results.json` 中的全量参考逐整数核对，并生成 `comparison.json`、`comparison.csv` 及各实验的输出。用户和 cache-aware 的每日冷启动分子也会核对；参考结果不会代替实际计算。

| 实验 | 默认数据范围及缓存历史 | 请求数 | 命中 token / 输入 token | 主结果 |
|---|---|---:|---:|---:|
| AgentX | 全量 393 会话，原会话及分支快照 | 98,827 | 21,206,048,768 / 21,635,381,376 | **98.015600%** |
| Sage session-id | 三天全量 trace，各规则 session 隔离 | 211,603 | 1,104,794,899 / 1,808,432,345 | **61.091304%** |
| Sage uid＋model | 三天可靠公共请求，跨天保留历史 | 211,538 | 1,340,479,688 / 1,808,432,150 | **74.123859%** |
| Sage cache-aware | 三天全量 schedule，4 副本各 50 万块，跨天保留历史 | 211,596 | 1,339,641,115 / 1,808,432,324 | **74.077481%** |

三天每日清空缓存的对照同样随运行保存，整体比例按输入 token 加权：

| 实验 | 每日清空的命中 token | 输入 token | 每日清空加权结果 | 跨天保留主结果 |
|---|---:|---:|---:|---:|
| uid＋model | 1,338,771,443 | 1,808,432,150 | 74.029399% | 74.123859% |
| cache-aware | 1,338,424,760 | 1,808,432,324 | 74.010221% | 74.077481% |

输入位于其他位置时：

```bash
python3 examples/run_all.py --agentx-input /path/to/traces.jsonl --sage-root /path/to/Sage-S2.1-flash --output-dir results/real-suite
```

如果分析的是另一份数据，使用 `--skip-reference-check` 跳过固定参考值检查，仍会根据实际输入计算。

## 1. AgentX 对应数据集

```bash
python3 examples/agentx.py
# 等价的显式配置：
python3 examples/agentx.py --input data/traces.jsonl --limit 393 --output-dir results/agentx-full
```

默认读取真实 `data/traces.jsonl` 的 393 个完整会话，使用原始 AgentX 算法。每个会话独立，子 agent 继承主线当时的历史快照，在自己的分支累积，不回写主线。

参考结果 **98.015600%**。默认 limit 为 393；合成示例和快速采样需要显式选择。

## 2. Sage 按 session-id

```bash
python3 examples/sage_session_id.py
# 默认使用三天：
python3 examples/sage_session_id.py --data-root data/sage --days 2026-11-16 2026-11-17 2026-11-18 --output-dir results/sage-session-full
```

读取三天的全部 trace 文件，每个规则 session 独立维护历史，结果为 **61.091304%**。这只表示规则分组隔离的实验口径，不把规则 session 认定为真实任务。日期及来源目录写入 summary。

## 3. Sage 按用户 ID＋模型

```bash
python3 examples/sage_user_id.py
# 默认使用三天，并同时计算跨天保留和每日冷启动：
python3 examples/sage_user_id.py --data-root data/sage --days 2026-11-16 2026-11-17 2026-11-18 --output-dir results/common
```

按共同字段及时间关系取 trace/schedule 的可靠公共请求，使用 trace 的 uid 和 schedule 的真实到达顺序。同一 uid、同一模型跨规则 session 及日期保留主结果的历史；不同 uid 和模型隔离。

主结果 `user_continuous` 为 **74.123859%**；`totals.user_daily_rate` 为此前的 **74.029399%**。输出逐请求明细、逐日结果和按用户汇总。用户缓存域不等同于任务。

公共请求数 211,538 少于 trace 和 schedule 全量，因为部分重复小请求无法可靠关联；不把未知 uid 猜成某个用户，参考表分别记录各自分母。

## 4. Sage 直接 cache-aware

```bash
python3 examples/sage_cache_aware.py
# 默认三天、4 副本、每副本 50 万块，跨天保留缓存及路由状态：
python3 examples/sage_cache_aware.py --data-root data/sage --days 2026-11-16 2026-11-17 2026-11-18 --capacity-blocks 500000 --replicas 4 --probe-blocks 256 --output-dir results/cache-aware-full
```

直接按日期、schedule 到达时间重放全部请求，不使用 session 或用户标签；不同模型隔离。路由器探测每个副本最多 256 块前缀，选最高命中长度；平局时按已分配请求数及轮转分配。

主结果 `results` 中的 `replicas-cache-aware` 为 **74.077481%**。`daily_cold_starts_weighted_results` 保存每日清空后加权的 **74.010221%**；`daily_results` 保存各日冷启动结果。两种历史范围均输出共享无限、共享 LRU、轮询、随机和 cache-aware 对照。

单共享 LRU 容量为 50 万块，多副本总容量为 200 万块，比较时须考虑容量差别。纯缓存亲和可能使流量倾斜，需一起检查副本请求数；命中率不代表吞吐。

## 显式日期子集与合成演示

真实实验默认三天。需要研究单日时，显式传入 `--days`；旧的单目录参数也保留：

```bash
python3 examples/sage_session_id.py --days 2026-11-18
python3 examples/sage_user_id.py --days 2026-11-18
python3 examples/sage_cache_aware.py --days 2026-11-18
# session 也支持 --trace-dir；cache-aware 也支持 --day-dir
```

没有真实数据、只想快速检查代码时，必须加 `--demo`：

```bash
python3 examples/run_all.py --demo
python3 examples/agentx.py --demo
python3 examples/sage_session_id.py --demo
python3 examples/sage_user_id.py --demo
python3 examples/sage_cache_aware.py --demo
```

演示模式才使用 [fixtures](fixtures)，其预期值分别为 40%、28.5714%、50%、64.2857%，仅验证代码逻辑。演示 cache-aware 默认 2 副本各 4 块；真实模式默认 4 副本各 50 万块。

所有 summary 都记录 `example_data_mode`。每次默认输出一个新目录；指定 `--output-dir` 时目录必须不存在。完整测试：`python3 -B -m unittest discover -s tests -v`。
