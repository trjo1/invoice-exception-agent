# Model Strategy — Open-Source First

**Status:** Locked for test phase. Reviewed quarterly.
**Date:** 2026-05-10 (V4-Flash defaults adopted 2026-05-11)
**Owner:** Tribhuvan Joshi

---

## The thesis

Open-weight models — **DeepSeek V4-Flash, DeepSeek V4-Pro**, Kimi K2, Qwen 2.5/3, Llama 3.3 70B — have reached agent-quality across the archetypes this project uses, at 90-95% lower cost than Anthropic Sonnet / OpenAI GPT-4o. We use only open models in the test phase. Closed models are reserved for per-task overrides in production when accuracy SLAs require it.

**V4 update (2026-05-11):** DeepSeek released V4 on 2026-04-24. V4-Flash (284B MoE, 13B active) costs $0.14/$0.28 per 1M tokens — roughly half the price of V3 — with a 1M-token context window. We adopted V4-Flash as the per-task default for extraction, classification, drafting, and corpus generation. V3 stays as fallback. V4-Pro is reserved for harder reasoning tasks once we benchmark it against R1.

Three reasons this discipline matters:

1. **Test runs are inherently cost-heavy.** The golden-cases regression set runs many times per day during build. At Sonnet pricing, a single golden-set run (40 cases × multi-step orchestration × thousands of tokens per step) costs $5-20. At DeepSeek V3 pricing, the same run costs $0.30-1. The cost difference accumulates fast.

2. **Portability is a buyer requirement.** Buyers in regulated industries (banking, healthcare, defense) want on-prem or self-hosted inference. An architecture that only works with closed APIs locks us out of those engagements. Building open-first means the agent can deploy anywhere.

3. **Quality gap is now manageable.** Late-2024 / early-2025 open releases (DeepSeek V3, Kimi K2, Qwen 2.5 72B) closed most of the quality gap to GPT-4o and Sonnet for the tasks this agent does (extraction, classification, reasoning, drafting). Where they still lag, we override per-task. We do not abandon the open-first default.

---

## Per-task model assignments (test phase defaults)

| Task | Default model | Why this model | API route |
|---|---|---|---|
| Corpus generation (synthetic invoices, emails) | **DeepSeek V4-Flash** | $0.14/$0.28 per 1M; 1M context; fine at JSON output. ~$0.18 for 500 invoices. | OpenRouter (`deepseek/deepseek-v4-flash`) |
| Invoice field extraction | **DeepSeek V4-Flash** | Strong on structured extraction with consistent JSON output. | OpenRouter |
| Supplier email parsing | **DeepSeek V4-Flash** | Same as above; conversational input is also in its sweet spot. | OpenRouter |
| Exception classification (12 categories) | **DeepSeek V4-Flash** | Multi-class classification is well-handled; outputs class + confidence + evidence. | OpenRouter |
| Decision-support reasoning (recommendation + rationale) | **DeepSeek R1** | Reasoning model; produces explicit chain-of-thought rationale. (V4-Pro is the next candidate once benchmarked.) | OpenRouter (`deepseek/deepseek-r1`) |
| Counterfactual generation ("if X were different...") | **DeepSeek R1** | Same as decision-support; counterfactual is a reasoning task. | OpenRouter |
| Agent orchestration (LangGraph node calls, tool use) | **Kimi K2** | Designed for agentic workflows. 128K context. Strong tool-calling. | Moonshot direct (cheapest) or OpenRouter (`moonshotai/kimi-k2`) |
| Supplier comms drafting | **DeepSeek V4-Flash** | Conversational quality acceptable; cheap; drafts are human-reviewed anyway. | OpenRouter |
| Internal status / escalation drafting | **DeepSeek V4-Flash** | Same. | OpenRouter |
| Embedding (RAG over policy + master data) | **bge-large-en-v1.5** | Strong open-source embedding; ~0 marginal cost. | OpenRouter or self-hosted |
| Re-ranking (RAG quality boost) | **bge-reranker-v2-m3** | Open-source cross-encoder reranker. | Self-hosted (no API needed) |

**Fallbacks** (kept in `config/models.yaml` for hot-swap if V4 routing has issues):
- `deepseek/deepseek-chat` (V3) — the previous default; available via the same OpenRouter route.
- `anthropic/claude-haiku-4` — for tasks where conversational tone matters more than cost.

**Fallbacks (closed models, used only when an explicit per-task override is set):**

| Closed model | When to use |
|---|---|
| Anthropic Claude Sonnet 4 | If decision-support accuracy lags below SLA on a regulated-industry deployment |
| Anthropic Claude Haiku 4 | If supplier drafting tone consistency lags (Haiku is strong on tone at low cost) |
| OpenAI GPT-4o | If a buyer requires it explicitly (some procurement processes have approved-vendor lists) |
| OpenAI text-embedding-3-small | If bge-large-en quality lags on a specific industry vocabulary |

---

## API access strategy

### Primary: OpenRouter

**`OPENROUTER_API_KEY`** — the primary credential.

OpenRouter is the single API for the open-model stack. One key, one base URL (`https://openrouter.ai/api/v1`), one OpenAI-compatible client. Models are addressed by string ID (`deepseek/deepseek-chat`, `moonshotai/kimi-k2`, `qwen/qwen-2.5-72b-instruct`). Model swaps are a config change, not a code change.

Pros: single contract, easy switching, model availability tracked centrally, fail-over routing built in.
Cons: ~5% markup over direct API costs. Worth it for test phase; revisit for production-scale spend.

### Secondary: Direct provider APIs (for production cost optimization)

| Provider | Direct API | When to switch |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | When monthly DeepSeek spend exceeds $500 — direct API saves the ~5% OpenRouter markup. |
| Moonshot (Kimi) | `https://api.moonshot.cn` | When Kimi K2 becomes a heavy-use model. China-based; check buyer geo-compliance before using. |
| Anthropic | `https://api.anthropic.com` | For closed-model fallbacks. |
| OpenAI | `https://api.openai.com` | For closed-model fallbacks. |

### Tertiary: Self-hosted (only when buyer requires)

For on-prem / air-gapped buyer deployments:
- DeepSeek V3 — 671B params, MoE, requires multi-GPU inference. Use `vLLM` or `SGLang`.
- Qwen 2.5 72B — 72B dense, fits on a single H100. Easier to deploy.
- Llama 3.3 70B — similar to Qwen 2.5 72B in deployment shape.

Build the buyer's inference stack only when explicitly required by procurement. Don't volunteer it.

---

## Cost ceiling and tracking

### Per-call ceiling

**$0.10 per LLM call** as a soft alert threshold. Any single call exceeding this triggers a logged warning. Common causes: long-context retrieval bloat, accidentally using the reasoning model where a chat model would do, missing prompt cache.

**$1.00 per LLM call** as a hard ceiling. Any call exceeding this is blocked by the model client; the calling code must explicitly opt in (`max_cost_usd=2.0`) to override. Prevents runaway loops.

### Per-task budget

| Step | Budget (test) | Budget (production target) |
|---|---|---|
| Single exception end-to-end | $0.50 | $1.00 (allows closed-model overrides) |
| Golden-set run (40 cases) | $10 | $40 |
| Daily test runs (3-5 runs typical) | $30-50 | $120-200 |

### Per-task instrumentation

Every LLM call goes through `src/p2p_agent/llm/client.py` which logs:

```json
{
  "timestamp": "2026-05-10T14:32:01Z",
  "task": "exception_classification",
  "model": "deepseek/deepseek-chat",
  "provider": "openrouter",
  "input_tokens": 1234,
  "output_tokens": 567,
  "input_cost_usd": 0.000123,
  "output_cost_usd": 0.000234,
  "total_cost_usd": 0.000357,
  "case_id": "GTC-002",
  "latency_ms": 1240
}
```

Aggregation script (`scripts/compute_stage9.py`) reads this and produces the cost-per-task Stage 9 signal.

---

## Quality gating

A model becomes the default for a task only after it clears:

| Gate | Threshold |
|---|---|
| Golden-set accuracy on that task | ≥ 80% of the closed-model baseline |
| P95 latency | ≤ 2× the closed-model baseline |
| JSON-output reliability (where the task requires structured output) | ≥ 95% valid JSON across 100 calls |
| Hallucination rate (RAG tasks) | Citation accuracy ≥ 90% |

If an open model fails any gate, fall back to the closed model for that specific task, log the decision in `docs/model_decisions.md`, and revisit when the open model has a new release.

**Quarterly review.** Every 90 days, re-benchmark the per-task defaults. Open models release fast; what's marginal today may be SOTA in three months.

---

## Per-engagement override path

Buyers may require specific model choices. Two override paths:

1. **Per-task override at deployment time.** Set `MODEL_OVERRIDE_<task>=<model>` in env config. Code unchanged.
2. **Closed-model only deployment.** Set `MODEL_PROVIDER=anthropic` or `=openai`. Every task uses the closed-model equivalent. Cost increases ~10x.

Document the override path used per engagement in the deployment runbook. This is reviewed in Phase 5 (Sustain) every quarter.

---

## Open questions

1. **Multilingual handling.** DeepSeek V3 is strong on Chinese and English; weaker on European languages. For EU buyers, may need to use Qwen 2.5 or fall back to Anthropic for supplier comms in French / German / Spanish. Benchmark when first EU-targeted golden cases land.

2. **Context window choice.** Kimi K2's 128K context tempts us into stuffing more context per call. The right answer is RAG retrieval, not context-stuffing. The model strategy should not become a license for sloppy retrieval design.

3. **Streaming vs batched.** Real-time HITL surfaces want streaming. Batch test runs don't. Default to streaming with a `stream=False` opt-out. May add provider-specific complications.

4. **Cost of structured output enforcement.** Some open models require explicit prompting for JSON output; some have a `response_format` parameter. The model client should normalize this so callers always get validated pydantic objects.

---

## Reference links

- DeepSeek API docs — `https://api-docs.deepseek.com/`
- OpenRouter model catalog — `https://openrouter.ai/models`
- Kimi K2 (Moonshot) — `https://platform.moonshot.cn/`
- BGE embeddings — `https://huggingface.co/BAAI/bge-large-en-v1.5`
- vLLM — `https://docs.vllm.ai/`
- SGLang — `https://github.com/sgl-project/sglang`
