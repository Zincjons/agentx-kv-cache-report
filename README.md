# AgentX 理论前缀命中率

这个工具用 Python 标准库在 CPU 上计算 InferenceX 数据集页的 `Cached input`。默认读取文件开头的 3 条完整会话。

## 运行

```powershell
python 'D:/AgentX-KV-Cache/analyze_agentx.py' --limit 3
```

每次运行会在项目的 `results` 下生成一个新目录。可以用 `--output-dir` 指定新目录，用 `--input` 指定 AgentX 格式的 JSONL。

- `requests.csv` 保存逐请求的会话 ID、会话内请求序号、分支、输入输出 token 数和命中 token 数。`trace_t_seconds` 保存原始请求的 `t` 字段。
- `summary.json` 保存样本范围、请求数、汇总分子分母、命中比例和 CPU 测量值。`cached_input_rate` 以 0 到 1 的比例保存。

## 统计口径

每条会话从空历史开始，沿 JSONL 中的请求原顺序处理。查询当前请求从开头连续命中的哈希块，然后把本轮输入的哈希加入对应历史。子 agent 从主线历史的副本起步，在自己的分支中累积。主线继续使用自己的历史。

单请求命中 token 数为 `min(输入 token 数, 命中块数 × block_size)`。汇总命中率为命中输入 token 总数除以输入 token 总数。

## CPU 测量

测量窗口从打开输入文件到逐请求 CSV 写入完成。`wall_seconds` 是经过的时间，`process_cpu_seconds` 是 Python 进程实际占用 CPU 的时间。两者相除得到单个逻辑核口径的平均占用，再除以机器的逻辑 CPU 数得到占整机容量的比例。

## 数据来源

- [AgentX 公开轨迹](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/tree/23f152f6f0f9399a85901b89a6458def0ef16729) 固定版本 `23f152f6f0f9399a85901b89a6458def0ef16729`。
- 本地 `data/traces.jsonl` 的 SHA-256 为 `29b6a19e751ff5230771519aab755f80a0f43a4ba9cf96b72d3a6a437ec99276`。
- [InferenceX 前缀和分支计算](https://github.com/SemiAnalysisAI/InferenceX-app/blob/1a191f6e242e54007551317fc26a2129a5726f21/packages/db/src/etl/weka-structure.ts#L100) 和 [汇总公式](https://github.com/SemiAnalysisAI/InferenceX-app/blob/1a191f6e242e54007551317fc26a2129a5726f21/packages/db/src/ingest-weka-dataset.ts#L232)。
