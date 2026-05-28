# Invoice generation — batch 53 of 100

You are generating realistic supplier-invoice data for a P2P (procure-to-pay) AI
agent test corpus. Each invoice you generate becomes input the agent's invoice
extractor will be tested against.

This batch contains 5 invoice specifications. For each one, generate
a complete invoice as a single JSON object matching the schema at the bottom.

**Critical rules:**
- Respond with a single JSON ARRAY containing exactly 5 invoice objects.
- No prose, no markdown, no commentary outside the JSON.
- Wrap the JSON in a ```json code block.
- Every field in the schema is required. Use realistic values consistent with
  the supplier persona for each invoice.
- When the spec says `error_injected: <error_mode>`, deliberately introduce
  that error so the test agent has something to catch. Record the error in
  `ground_truth_note`. When `error_injected: null`, the invoice is clean.

---

## Invoices to generate

### Invoice 1
- persona_id: P004
- persona_name: Mumbai Materials Co.
- region: APAC-IN
- language: en
- currency: INR
- layout_style: legacy_pdf
- payment_terms: NET-60
- line_items: about 3
- invoice_date around: 2026-03-15
- error_injected: GST percentage occasionally misrouted between IGST and CGST/SGST
- persona quirks to reflect:
      - GST itemized; HSN codes per line
      - PAN + GSTIN in header
      - Cross-currency to USD common

### Invoice 2
- persona_id: P005
- persona_name: São Paulo Componentes Ltda.
- region: LATAM-BR
- language: pt
- currency: BRL
- layout_style: scanned_pdf
- payment_terms: NET-30
- line_items: about 5
- invoice_date around: 2026-01-03
- error_injected: Date format dd/mm/yyyy vs ISO
- persona quirks to reflect:
      - NF-e (Brazil tax document) shape — invoice + tax receipt combined
      - Multiple tax types (ICMS, IPI, PIS, COFINS)
      - Boleto bancário payment slip referenced

### Invoice 3
- persona_id: P003
- persona_name: Frankfurt Industriebedarf GmbH
- region: EU-DE
- language: de
- currency: EUR
- layout_style: structured_pdf
- payment_terms: NET-30
- line_items: about 8
- invoice_date around: 2026-04-27
- error_injected: VAT field missing on intra-EU shipments to non-DE buyers
- persona quirks to reflect:
      - DE VAT (19%) itemized; reverse-charge logic on intra-EU
      - Bilingual labels (German primary, English secondary)
      - SEPA bank details on every invoice

### Invoice 4
- persona_id: P004
- persona_name: Mumbai Materials Co.
- region: APAC-IN
- language: en
- currency: INR
- layout_style: legacy_pdf
- payment_terms: NET-60
- line_items: about 3
- invoice_date around: 2026-04-26
- error_injected: null (clean invoice)
- persona quirks to reflect:
      - GST itemized; HSN codes per line
      - PAN + GSTIN in header
      - Cross-currency to USD common

### Invoice 5
- persona_id: P002
- persona_name: Maple Logistics SMB
- region: US
- language: en
- currency: USD
- layout_style: scanned_pdf
- payment_terms: NET-45
- line_items: about 2
- invoice_date around: 2026-02-15
- error_injected: null (clean invoice)
- persona quirks to reflect:
      - Hand-written notes occasionally on scanned invoices
      - Inconsistent header placement
      - Round totals up to nearest dollar

---

## Output schema (one object per invoice)

```json
{
  "invoice_id": "string \u2014 format: <persona_id>_INV_<YYYYMMDD>_<NNN>",
  "persona_id": "string \u2014 the persona this invoice is from",
  "po_reference": "string \u2014 format: PO-<YYYY>-<MM>-<NNNNN>",
  "invoice_date": "ISO date \u2014 YYYY-MM-DD",
  "currency": "ISO currency code (USD, EUR, GBP, INR, BRL, etc.)",
  "payment_terms": "string \u2014 e.g. NET-30, NET-45, 2/10-NET-30",
  "header_fields": {
    "vendor_name": "string \u2014 matches persona name",
    "vendor_address": "string \u2014 multi-line OK, plausible for persona's region",
    "vendor_tax_id": "string \u2014 region-appropriate (US EIN, EU VAT ID, IN GSTIN, etc.)",
    "buyer_name": "string \u2014 invented buyer organization, kept consistent across batches",
    "buyer_address": "string",
    "buyer_po_contact": "string \u2014 name + email"
  },
  "line_items": [
    {
      "line_no": "integer, 1-indexed",
      "sku": "string \u2014 supplier-specific SKU",
      "description": "string \u2014 concrete product/service description",
      "quantity": "number",
      "unit_price": "number \u2014 in invoice currency",
      "line_total": "number \u2014 quantity \u00d7 unit_price; rounding per persona quirk"
    }
  ],
  "subtotal": "number \u2014 sum of line totals",
  "tax": [
    {
      "jurisdiction": "string \u2014 e.g. US-CA, EU-DE-VAT, IN-IGST",
      "rate": "number 0\u20131",
      "amount": "number"
    }
  ],
  "total": "number \u2014 subtotal + tax",
  "error_injected": "string|null \u2014 which error mode was deliberately injected, e.g. 'PO_REFERENCE_TYPO', 'MISSING_TAX_LINE', null if clean",
  "ground_truth_note": "string \u2014 1-2 sentences explaining what the extractor should pull and any deliberate errors"
}
```

---

## Reminder

Output: a single JSON array of 5 objects, in a ```json block. Nothing else.
