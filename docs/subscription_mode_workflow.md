# Subscription Mode Workflow — generating the corpus via Claude.ai / ChatGPT

**Status:** **FALLBACK** as of 2026-05-11. API mode (DeepSeek V4-Flash via OpenRouter) is now the default — see `model_strategy.md` and `scripts/generate_invoices.py`. The full 500-invoice corpus runs for ~$0.18 in API mode, which makes the original cost argument for subscription mode moot. This doc is retained for the case where OpenRouter is unavailable, a budget cap is hit, or a buyer requires zero outbound API traffic during a particular engagement.

**Original status (kept for history):** Locked for initial corpus build (per test_corpus_design.md §6 decision #2).
**Date:** 2026-05-10
**Owner:** Tribhuvan Joshi

---

## Why this exists (original framing, 2026-05-10)

API costs for generating ~500 invoices + ~200 emails + master data through Anthropic / OpenAI APIs would run $50-150 in the test phase. TJ has personal Claude Pro and ChatGPT Pro subscriptions ($20/month each, already paid). Their terms allow commercial use of generated output and their usage quotas are generous enough to handle the full corpus build with zero marginal cost.

## Why API mode replaced this as the default (2026-05-11)

DeepSeek V4-Flash (released 2026-04-24) costs $0.14/$0.28 per 1M tokens — roughly 4x cheaper on output than V3 and ~20x cheaper than Claude Sonnet. The full 500-invoice corpus runs for **$0.18**, not $50-150. That's not "save $50 vs $0", it's "spend $0.18 to remove 6 hours of TJ paste-time and make the pipeline reproducible from a seed." Different tradeoff than the one that drove the original lock.

The subscription-mode pipeline below still works as written. The only thing that's changed is which one runs by default. See `docs/model_strategy.md` and `scripts/generate_invoices.py` (the API-mode runner) for the current default.

## When to use subscription mode anyway

- OpenRouter is unavailable or rate-limited.
- A buyer requires no outbound API traffic during a specific engagement.
- TJ is offline or wants to generate corpus while traveling without burning the API budget.

---

## Original flow (subscription mode)

So instead of API calls, the corpus generation pipeline is split into two halves:

1. **Prompt generator (automated)** — Python script reads `config/personas.yaml`, produces prompts in batches, writes them to disk as Markdown files ready to copy-paste into Claude or ChatGPT.
2. **Human-in-the-loop (TJ)** — TJ opens each batch prompt, pastes into Claude.ai or ChatGPT, waits for the response, copies the response back into the matching response file.
3. **Response ingester (automated)** — second Python script parses the responses and writes the final invoice PDFs / email files / master data records to `test_corpus/synthetic/`.

The script never hits an API. The chat UI does the work, charged against the subscription, not the API.

---

## The flow

```
   config/personas.yaml
           │
           ▼
   ┌──────────────────────────────┐
   │ scripts/generate_invoice_    │
   │   prompts.py                 │
   │                              │
   │ Reads personas; generates    │
   │ batched prompts; writes to   │
   │ scripts/subscription_        │
   │   workflow/prompts/          │
   └─────────────┬────────────────┘
                 │
                 ▼
         prompts/invoice_batch_001.md
         prompts/invoice_batch_002.md
         …
                 │
                 │  [TJ pastes each prompt into Claude.ai / ChatGPT;
                 │   copies the response back]
                 ▼
         responses/invoice_batch_001.json
         responses/invoice_batch_002.json
         …
                 │
                 ▼
   ┌──────────────────────────────┐
   │ scripts/ingest_subscription_ │
   │   responses.py               │
   │                              │
   │ Parses each response JSON,   │
   │ validates against the schema,│
   │ renders invoice PDFs via     │
   │ weasyprint, writes to        │
   │ test_corpus/synthetic/.      │
   └──────────────────────────────┘
```

---

## Batch sizing

| Asset type | Items per batch | Batches | Total time per batch (paste + wait) |
|---|---|---|---|
| Invoices (structured JSON for ~5 invoices) | 5 | 100 batches → 500 invoices | ~3 minutes per batch |
| Supplier emails (~4 threads) | 4 | 50 batches → 200 emails | ~3 minutes per batch |
| Master data (vendors batched by region) | 20 vendors | 10 batches → 200 vendors | ~2 minutes per batch |

**Total TJ time to generate the full corpus:** ~6 hours spread across however many sessions. Each batch is self-contained — you can stop and resume anytime.

---

## Which model to use for each asset

| Asset | Claude.ai (Claude Sonnet/Opus) | ChatGPT (GPT-4 / GPT-5) | Recommendation |
|---|---|---|---|
| Invoice JSON generation | Strong; Artifacts feature handy for inline JSON | Strong; canvas mode similar | **Claude.ai** — Artifacts make verifying JSON validity easier |
| Supplier email threads | Strong on tone, multilingual, dialogue shape | Strong but more verbose | **Claude.ai** for English/German; **ChatGPT** for Portuguese/Hindi (tends to handle non-Latin scripts a bit better in casual writing) |
| Master data records | Both work; structured JSON | Both work; structured JSON | Either; alternate to spread load |

If you hit a rate limit, switch tools and resume.

---

## Step-by-step — generate the first batch of invoices

### 1. Generate the prompts

```bash
make corpus-prompts-invoices
# or
uv run python scripts/generate_invoice_prompts.py \
    --count 500 \
    --batch-size 5 \
    --seed 42
```

This writes 100 files to `scripts/subscription_workflow/prompts/invoice_batch_001.md` through `invoice_batch_100.md`. Each file is self-contained — has the persona context, the requested invoice variations, and the JSON schema the response must follow.

### 2. Process batches in chat

For each batch file:

1. Open `scripts/subscription_workflow/prompts/invoice_batch_NNN.md` in your editor.
2. Open Claude.ai in your browser. Start a new chat (or continue an existing project chat for the corpus generation work).
3. Copy the entire prompt file content.
4. Paste into Claude. Send.
5. Claude responds with the JSON Artifact. Verify it looks structurally right (5 invoices, fields present).
6. Click the Artifact's copy button, or copy the JSON output.
7. Paste into `scripts/subscription_workflow/responses/invoice_batch_NNN.json`.
8. Save the file.

**Tip — keep a single Claude.ai chat per "session."** You can paste batch 1, get the response, then paste batch 2 in the same chat. Claude remembers the persona context and produces more consistent invoices. Start a fresh chat every 20-25 batches to keep context manageable.

### 3. Ingest the responses

After processing some or all batches:

```bash
make corpus-ingest-invoices
# or
uv run python scripts/ingest_subscription_responses.py --asset invoices
```

This:
- Reads every JSON file in `responses/`
- Validates the schema for each invoice
- Renders the PDF via weasyprint
- Writes `test_corpus/synthetic/invoices/<persona_id>_INV_NNNNNNN.pdf`
- Writes a sidecar `.json` with the ground truth (what the extractor should pull)
- Writes a batch log to `scripts/subscription_workflow/batch_logs/`

You can run ingestion incrementally as you finish batches. The script is idempotent — re-running it skips already-ingested batches.

---

## Prompt design

The prompts are deliberately strict on output format. The chat UI is reliable enough at JSON output that we don't need provider-specific structured-output features.

**Required output shape per invoice (enforced by the ingester):**

```json
{
  "invoice_id": "P001_INV_20260420_001",
  "persona_id": "P001",
  "po_reference": "PO-2026-04-00134",
  "invoice_date": "2026-04-20",
  "currency": "USD",
  "payment_terms": "NET-30",
  "header_fields": {
    "vendor_name": "Global Tech Components Inc.",
    "vendor_address": "...",
    "buyer_name": "ACME Industrial",
    "buyer_address": "..."
  },
  "line_items": [
    {
      "line_no": 1,
      "sku": "SKU-A445",
      "description": "Industrial sensor module",
      "quantity": 100,
      "unit_price": 25.00,
      "line_total": 2500.00
    }
  ],
  "subtotal": 2500.00,
  "tax": [{"jurisdiction": "US-CA", "rate": 0.0725, "amount": 181.25}],
  "total": 2681.25,
  "error_injected": null,
  "ground_truth_note": "Standard invoice, no errors injected."
}
```

The prompt template (in `scripts/generate_invoice_prompts.py`) inflates the persona's quirks and the error-injection plan into clear English instructions for the chat to follow.

---

## Failure recovery

| Failure | Recovery |
|---|---|
| Claude / ChatGPT returns malformed JSON | Re-paste the prompt; ask "please respond with valid JSON only, no surrounding text." If still bad, escalate to a different model (switch Claude ↔ ChatGPT). |
| Claude / ChatGPT refuses to generate (rare on Pro) | Re-phrase the prompt slightly; if persistent, that prompt batch is malformed — open an issue and fix the prompt template. |
| Rate limit hit | Switch to the other tool; resume later. Both Claude Pro and ChatGPT Pro have very generous limits — hitting one usually means you've done a few hundred messages today. |
| Response is partially correct | The ingester validates and reports specifically which fields failed; fix that one batch and re-ingest. |
| Lose the response file accidentally | Re-process that batch (prompts are deterministic from the seed). |

---

## When to switch out of subscription mode

Move to API mode (via OpenRouter) when:

- Corpus generation enters a maintenance phase (continuous incremental generation) and TJ's manual paste time is the bottleneck.
- The first paying engagement provides budget for automated generation.
- A buyer requires reproducible-on-demand corpus regeneration (e.g., a compliance audit).

At that point, `scripts/generate_invoices.py --mode api` (already stubbed) replaces this workflow. The output schema is identical so the ingester works either way.

---

## Subscription terms — quick sanity check

- **Claude Pro / Team / Enterprise:** Anthropic's terms (anthropic.com/legal/commercial-terms) allow commercial use of outputs. Outputs are owned by the subscriber.
- **ChatGPT Plus / Pro / Team:** OpenAI's terms (openai.com/policies/terms-of-use) assign output ownership to the user; commercial use is permitted.

Both are fine for commercial-IP work. Document the workflow choice in `docs/CHANGELOG.md` for audit trail.
