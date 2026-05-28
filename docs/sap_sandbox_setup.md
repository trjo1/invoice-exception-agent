# SAP S/4HANA Cloud Trial — setup guide

**Status:** Locked as primary ERP sandbox for the agent build (per
test_corpus_design.md §6 decision #3).
**Date:** 2026-05-10
**Owner:** Tribhuvan Joshi

---

## Why SAP first

SAP S/4HANA has the largest P2P customer base in our target buyer segment (F1000). The OData v2/v4 APIs are well-documented and the trial environment exposes the same surface as production. Building the SAP connector first lets us validate the abstract connector pattern (`src/p2p_agent/connectors/base.py`) against the most realistic constraint set; Dynamics 365 and ServiceNow connectors come behind it as portability checks.

Microsoft Dynamics 365 Finance trial signup happens in week 4 of the build (per the test corpus design build sequence) to validate the connector abstraction holds across two distinct ERP families.

---

## Two trial paths — pick one

SAP offers two free options. Both work for our use case; the second is more durable.

### Option A — SAP S/4HANA Cloud Public Edition trial (recommended)

- 30-day trial, renewable.
- Hosted by SAP; nothing to install.
- Pre-loaded with sample master data, POs, vendors, GL accounts.
- Full OData API access.
- Closest to what an F1000 buyer's production system looks like.

**Signup:** https://www.sap.com/products/erp/s4hana/trial.html

Fields to expect on the form:
- Work email (any active email)
- Company name (any active company name; SAP doesn't validate against a registry)
- Country
- Industry: Professional Services
- Use case: "AI agent development and testing"

After signup, SAP emails the trial-tenant URL and a system admin login within ~24 hours.

### Option B — SAP HANA Cloud + S/4HANA Developer Edition (durable backup)

- Free, persistent (does not expire).
- Self-installable; runs locally or on a small VM.
- Real S/4HANA APIs but you bring the master data.
- Higher setup cost (~3-4 hours of one-time install) but no trial renewal anxiety.

**Signup:** https://developers.sap.com/tutorials/btp-app-hana-cloud-setup.html

Choose this if you want to avoid the 30-day renewal cycle, or if option A's trial renewal becomes friction. The same connector code works against either.

---

## Recommended path

**Start with option A.** Get a working connection within a day. Validate the OData read flow against pre-loaded data. If we're still actively building 25 days in, set up option B in parallel as the durable backup.

---

## Step-by-step — option A

### 1. Register

- Visit https://www.sap.com/products/erp/s4hana/trial.html.
- Fill the form. Use your own email.
- Wait for the welcome email with tenant URL + admin credentials.

### 2. Capture credentials

SAP issues an OAuth 2.0 client credentials flow against a service-user account. From the BTP cockpit:

1. Go to your tenant subaccount.
2. Navigate to Security → Service instances.
3. Create a new service instance of type "SAP S/4HANA Cloud API".
4. Generate a service key — this returns:
   - `client_id`
   - `client_secret`
   - `token_url` (OAuth endpoint)
   - `api_url` (base URL for OData)
5. Copy these to `.env`:
   ```
   SAP_BASE_URL=https://api.s4hana-cloud.cfapps.<region>.hana.ondemand.com
   SAP_TOKEN_URL=https://<tenant>.authentication.<region>.hana.ondemand.com/oauth/token
   SAP_CLIENT_ID=...
   SAP_CLIENT_SECRET=...
   ```

### 3. Validate the connection

```bash
make sap-validate
```

This runs `scripts/validate_sap_connection.py`. The script:
- Reads the env vars
- Does the OAuth 2.0 client_credentials flow against `SAP_TOKEN_URL`
- Issues a single read against the Purchase Order OData service (`/sap/opu/odata/sap/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder`)
- Reports success or the specific failure (invalid creds, network, scope missing)

Until you've done step 2, the script will fail with a clear message telling you what's missing.

---

## OData services we'll use

The agent's SAP connector uses these services. All are in the standard S/4HANA Cloud API catalog (`https://api.sap.com/api/<service>/overview`).

| Service | Path | Used for |
|---|---|---|
| API_PURCHASEORDER_PROCESS_SRV | /A_PurchaseOrder | Read PO header + line items |
| API_SUPPLIERINVOICE_PROCESS_SRV | /A_SupplierInvoice | Read + post supplier invoices (HITL-gated) |
| API_MATERIAL_DOCUMENT_SRV | /A_MaterialDocumentHeader | Read goods receipt records |
| API_BUSINESS_PARTNER | /A_BusinessPartner | Vendor master read |
| API_GLACCOUNTLINEITEM_RAW | /A_GLAccountLineItemRawItem | GL line-item read for matching |
| API_FIXEDASSET_SRV | /A_FixedAsset | (Phase 2) Fixed-asset PO linkage |

Per-service authorization scopes are issued at service-key creation time. Start with read-only scopes; add write scopes (for SupplierInvoice post) only after HITL flow tests pass.

---

## Cost

SAP S/4HANA Cloud trial: **$0** for 30 days. Renewable for another 30 days a few times before they ask you to convert to paid. Total free runway: ~3-6 months depending on renewal cadence.

If we hit the renewal limit before the first paying engagement, switch to option B (HANA Cloud Developer Edition, free forever) and migrate the connector tests.

---

## Common failure modes

| Failure | Likely cause | Fix |
|---|---|---|
| "401 Unauthorized" on first call | Token URL wrong, or scope missing on service key | Verify `SAP_TOKEN_URL` matches the BTP cockpit; regenerate service key with all scopes |
| Tenant expired silently | 30-day trial elapsed | Renew from BTP cockpit; renew at least once per 25 days |
| "OData service not found" | Service not added to the service-key allowlist | Add the service in BTP cockpit, regenerate key |
| Rate limit (429) under test load | Free tier has soft rate limits | Add `tenacity` retry with exponential backoff in connector |
| TLS error | Region-specific endpoint, not the global one | Use the region-specific `api_url` from the service key, not the documentation example |

---

## When to expand to Dynamics 365 Finance

Per the build sequence (test_corpus_design.md §5):

- Weeks 1-3: SAP only. Build connector depth.
- Week 4: Sign up for D365 Finance trial; build the connector against the abstract base. Goal: validate that the connector pattern holds across two ERP families.
- Weeks 5-8: Both connectors live alongside ServiceNow and a stub Ariba connector.

---

## When to expand to Ariba

Ariba is a separate developer signup (https://developer.ariba.com/api). Defer to week 6-8 unless a design partner specifically needs Ariba on day one.

---

## What the validator script does

```bash
make sap-validate
```

Calls `scripts/validate_sap_connection.py`. The script:

1. Loads env from `.env` via pydantic-settings.
2. Reports which SAP env vars are set vs missing (clear, no guessing).
3. If all set, does the OAuth `client_credentials` flow against `SAP_TOKEN_URL`.
4. Issues a single GET against `/A_PurchaseOrder` with `?$top=1` to verify read access.
5. Prints the response status, schema sample, and a thumbs-up.
6. If anything fails, prints the specific HTTP status + body so you can fix it.

Run this every time you set up a new trial tenant. Run before any integration test to confirm the connection is live.

---

## Reference links

- SAP S/4HANA Cloud trial signup — https://www.sap.com/products/erp/s4hana/trial.html
- SAP API Business Hub — https://api.sap.com/
- S/4HANA Cloud OData API catalog — https://api.sap.com/package/SAPS4HANACloud
- BTP cockpit (manage trials) — https://account.hana.ondemand.com/
- Developer Edition (option B) — https://developers.sap.com/tutorials/btp-app-hana-cloud-setup.html
