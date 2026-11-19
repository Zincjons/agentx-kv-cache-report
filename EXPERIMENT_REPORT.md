# AgentX KV Cache 理论前缀命中率实验报告

对 AgentX 全量请求轨迹采用 InferenceX 的 `Cached input` 口径进行离线计算，理论前缀命中率为 **98.0156%**，与 InferenceX 数据集页的统计一致。该值表示在历史前缀能够持续保留的条件下，可复用输入 token 占总输入 token 的比例。 [InferenceX 数据集页](https://inferencex.semianalysis.com/infx/agentx/cc-traces-weka-062126)

## 数据集

| 项目 | 内容 |
| --- | --- |
| 数据集 | `semianalysisai/cc-traces-weka-062126`（full） |
| 固定版本 | `23f152f6f0f9399a85901b89a6458def0ef16729` |
| 本地文件 | `D:/AgentX-KV-Cache/data/traces.jsonl` |
| 文件格式 | JSONL，每行一条原始 Claude Code 会话，包含主线请求和子 agent 分支 |
| 实验范围 | 393 条会话，共 98,827 次模型调用，其中 56,798 次主线调用和 42,029 次子 agent 调用 |

每块代表 **64 个输入 token**。这版数据的 `hash_id_scope` 全部为 `local`，因此每条会话使用自己的块编号表。同一会话内，`hash_ids` 按输入顺序保存块编号，并保留块与前面上下文的前缀关系。 [固定版本数据集](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/tree/23f152f6f0f9399a85901b89a6458def0ef16729) · [AgentX 匿名化方法](https://newsletter.semianalysis.com/p/agentx-inferencexv3-does-cuda-moat)

## 数据集样例

下面截取本地文件第 1 行会话的第 1 条主线请求。原始文件每行保存一条完整会话，这里展开排版并节选请求列表。

```json
{
  "id": "002001296e8a8c38ad9d7cc436d691afc602",
  "models": ["claude-haiku-4-5-20251001", "claude-opus-4-8"],
  "block_size": 64,
  "hash_id_scope": "local",
  "requests": [
    {
      "type": "s",
      "t": 0.0,
      "model": "claude-opus-4-8",
      "in": 640,
      "out": 20,
      "hash_ids": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
      "api_time": 1.979,
      "ttft": 1.741
    }
  ]
}
```

这条调用是流式请求（`type: "s"`）。轨迹记录了 640 个输入 token 和 20 个输出 token，输入对应 10 个块，每块 64 token。完整会话还可能包含 `type: "subagent"` 的分支组，分支内的模型调用保存在该组的 `requests` 中。 [Weka 数据格式](https://docs.nvidia.com/aiperf/v0.13.0/tutorials/datasets-inputs/replay-weka-agentic-coding-traces#what-is-a-weka-trace)

## 测试公式

对每条会话建立一份空的主线历史，按文件中 `requests` 的原始顺序处理。每次从 `hash_ids` 开头检查连续已见块，遇到第一个未见块即停止。完成计分后，把本次请求的输入哈希加入历史。处理子 agent 时，从当时主线历史的副本起步，在分支内累积。主线恢复后继续使用自己的历史。 [InferenceX 前缀与分支计算](https://github.com/SemiAnalysisAI/InferenceX-app/blob/1a191f6e242e54007551317fc26a2129a5726f21/packages/db/src/etl/weka-structure.ts#L100)

对第 `i` 条请求，`I_i` 为轨迹记录的输入 token 数（`in`），`B_i` 为会话块大小，`K_i` 为从开头连续命中的块数。全量指标按所有模型调用的输入 token 加权，主线和子 agent 内的调用都参与汇总。 [InferenceX 汇总公式](https://github.com/SemiAnalysisAI/InferenceX-app/blob/1a191f6e242e54007551317fc26a2129a5726f21/packages/db/src/ingest-weka-dataset.ts#L232)

```text
单请求可复用输入 token  H_i = min(I_i, K_i × B_i)
理论前缀命中率             = (Σ H_i ÷ Σ I_i) × 100%
```

样例会话第 2 条主线请求有 996 个输入块。第 3 条请求有 1,022 个输入块，开头 996 个块与第 2 条请求相同，因此第 3 条请求可复用 `996 × 64 = 63,744` 个输入 token。它的单请求命中率为 `63,744 ÷ 65,408 ≈ 97.456%`。 [逐请求明细](D:/AgentX-KV-Cache/results/full/requests.csv)

## 全量结果与复现

| 指标 | 数值 |
| --- | ---: |
| 总输入 token | 21,635,381,376 |
| 可复用输入 token | 21,206,048,768 |
| 理论未命中的输入 token | 429,332,608 |
| 读取数据到写完 CSV 的耗时 | 44.66 秒 |
| Python 进程的 CPU 时间 | 44.48 秒 |
| 分析进程的平均 CPU 占用 | 单逻辑核的 99.6%，折合 16 个逻辑核总容量的 6.23% |

98,827 行逐请求明细的 token 加总与汇总 JSON 一致，可复用输入 token 和总输入 token 的整数值也与 InferenceX 一致。 [实验汇总](D:/AgentX-KV-Cache/results/full/summary.json)

在本地使用已下载的 JSONL 文件运行以下命令即可复现。

```powershell
python 'D:/AgentX-KV-Cache/analyze_agentx.py' --limit 393
```
