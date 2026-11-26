# Synthetic fixtures

These are hand-written examples, not production traces or anonymized excerpts.

- `agentx.jsonl`: 2 conversations, 6 calls including one child branch;
  960 input tokens and 384 cached tokens under the original AgentX rules.
- `sage/2026-11-18/Sage-S2.1-flash/`: 5 requests, 896 input tokens.
  Two `71-*` rule groups intentionally split one user across files.
  Group-isolated history caches 256 tokens; user/model history caches 448;
  a shared model cache caches 576.

Small integer IDs illustrate hash equality. Sage IDs are comparable across its
synthetic rule groups; AgentX IDs are local to each separate conversation.
