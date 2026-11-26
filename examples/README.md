# 四个 example 的运行与预期结果

默认输入来自 [fixtures](fixtures)，全部为手工合成小数据，不是生产摘录。所有命令均在仓库根目录运行。各 example 的 `--help` 提供参数说明。

## 1. AgentX 分析

```bash
python3 examples/agentx.py
```

两个会话、6 次模型调用、960 个输入 token。第一个会话包括子 agent：

1. 主线 `[0,1]` 命中 0。
2. 主线 `[0,1,2]` 命中 128 token。
3. 子 agent 继承主线历史快照，`[0,3]` 命中 64 token。
4. 主线 `[0,3,4]` 只命中 64 token，因为子 agent 见过的块 3 没有回写。
5. 第二个会话从空历史开始，两次调用合计命中 128 token。

最终 **384 / 960 = 40%**。不同会话即使出现相同数字 ID，也不共享局部编号历史。

全量：

```bash
python3 examples/agentx.py --input data/traces.jsonl --limit 393 --output-dir results/agentx-full
```

## 2. Sage 按 session-id 分析

```bash
python3 examples/sage_session_id.py
```

5 次请求、896 个输入 token，分为 `71-00000`、`71-00001`、`440-00000`。每个文件独立历史，调用原始 AgentX 算法，结果为 **256 / 896 = 28.5714%**。两个 `71-*` 文件在这里不共享历史。

这演示的是规则分组隔离，不是真实任务划分。全量一天：

```bash
python3 examples/sage_session_id.py --trace-dir data/sage/2026-11-18/Sage-S2.1-flash/trace --output-dir results/sage-session-18
```

三天规则分组和用户参考可同时计算：

```bash
python3 -m experiments.trace_users --data-root data/sage --output-dir results/trace-reference
```

## 3. Sage 按用户 ID 分析

```bash
python3 examples/sage_user_id.py
```

先按完整共同字段匹配 trace 与 schedule，必要时用时间原点消歧。重复请求按出现次数处理；次数或用户归属无法确认的部分单列排除，不做多对多笛卡尔连接。

按 `(uid,model)` 累积历史：两个 `71-*` 文件共用用户 71 的历史；用户 440 从自己的空历史开始。

- 用户 71 命中 `0 + 128 + 192 = 320` token。
- 用户 440 命中 `0 + 128 = 128` token。
- 合计 **448 / 896 = 50%**。

全量三天：

```bash
python3 examples/sage_user_id.py --data-root data/sage --days 2026-11-16 2026-11-17 2026-11-18 --output-dir results/common
```

uid 是 API 用户标识；同用户可能有多个客户端或任务。本例定义缓存域，不生成真实任务标签。

## 4. 直接 cache-aware 分析

```bash
python3 examples/sage_cache_aware.py
```

直接读取 schedule，不读 session 或用户标签，不同模型仍隔离。默认 2 个副本，每副本 4 块：

| 策略 | 命中 token | 命中率 |
|---|---:|---:|
| 单共享无限缓存 | 576 / 896 | 64.2857% |
| 单共享 LRU，4 块 | 576 / 896 | 64.2857% |
| 轮询 | 384 / 896 | 42.8571% |
| cache-aware 前缀路由 | 576 / 896 | 64.2857% |

还输出固定种子的随机路由结果。路由器优先选择前缀命中最长的副本，默认最多探测 256 块。平局先选已接收请求最少的副本，再轮转，避免零分时固定堆到副本 0。

纯缓存亲和不保证负载均衡：这个小样例的前缀相似，5 次请求均送到一个副本，输出 `[5,0]`。不能据命中率相同推断吞吐相同。

全量数据应显式增大示例默认的 4 块容量：

```bash
python3 examples/sage_cache_aware.py --day-dir data/sage/2026-11-18/Sage-S2.1-flash --capacity-blocks 500000 --replicas 4 --probe-blocks 256 --output-dir results/cache-aware-18
```

容量单位为块，每块 64 token。在未知模型 KV 字节数时不能换算显存 GB。本例模拟缓存命中和 LRU，不模拟并发执行队列或吞吐。

## 输出

每例生成 `summary.json`；AgentX/session/user 还生成逐请求 CSV。默认目录带随机后缀；显式 `--output-dir` 必须不存在。用 `python3 -B -m unittest discover -s tests -v` 验证全部预期值。
