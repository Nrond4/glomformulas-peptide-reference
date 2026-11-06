# Verification and research rules

## Product catalog

`CatalogCrawler` identifies a supported public catalog from the actual homepage or an explicitly selected mode. WooCommerce scans require consistent declared totals; Shopify scans require terminating pagination and unique IDs. Sitemap scans walk the declared sitemap scope, inspect product structured data, and refuse unreadable product paths or incomplete pagination. Catalog robots exclusions, host escapes, failed pages, malformed data and crawl limits stop the scan. A sitemap scan covers published sitemap products, not a guarantee that the merchant has no hidden/unlisted products. A custom JavaScript-only catalog may require a site-specific rendered adapter once its actual structure can be inspected.

Comparisons preserve scan scope. Switching between a sitemap and a public API requires a deliberate baseline review, rather than treating a coverage difference as a discontinuation. A first complete snapshot establishes a baseline. Absent products are discontinuation candidates for review; they are never deleted automatically. Sizes preserve their units and printed labels; mass, concentration, IU and blend ratios are not treated as interchangeable.

## Freedom Diagnostics

The official lookup observed during implementation supports code/accession and company searches. It explains that search codes are case-insensitive and spaces can be removed. The connector canonicalizes those characters, then accepts only codes beginning with GLO and a nonempty suffix. The original printed values and report text remain available. Non-GLO reports returned by a broad company search cannot enter the GloFormulas report collection.

Official interface reference: https://freedomdiagnosticstesting.com/search-for-your-coa-based-on-the-unique-accession-number/

Static results can be read directly; dynamic results use the optional rendered public lookup. No hidden API, invented search endpoint, login bypass or CAPTCHA solving is assumed. An unreadable lookup, no visible PDF links, or changed form is a blocker, not proof of no testing. Company discovery currently has no confirmed live pagination contract: it can collect readable links but cannot claim all GloFormulas reports have been found. Known printed codes can be checked individually.

Only PDFs downloaded from the exact allowlisted official lab hosts qualify for automated lab-host authenticity status. Bytes are hashed and retained privately, and extracted text records its page. Missing/conflicting fields are flagged rather than filled by inference. Scanned/image-only reports require OCR/manual review. The parser currently recognizes explicit labels; its handling of the lab's actual current and historical layouts still needs validation against real official reports. An automatic extraction is not a manual sign-off.

A lab-hosted GLO report is still separate from a verified association with a particular GloFormulas product/batch. A reviewed expectation has `product_id`, `identity`, `batch`, `linkage_source_id`, and `linkage_reviewed_by`. Its linkage source must be an existing catalog/vendor batch record on the official GloFormulas host; printed company, identity and batch must match exactly. Unresolved aliases, missing batch evidence, and conflicts stay pending. Supplier reports are never promoted by this process.

The review engine records report date, search code, batch, lab, identity, purity, measured content and raw values. It flags mismatches, duplicates, missing fields, future dates, qualified purity, content-unit conflicts and unusual quantities. Default review triggers (purity below 98%, content more than 10% from a declared comparable amount) are review heuristics, not manufacturer specifications or clinical safety thresholds. Blend component evidence is not a combined-vial report.

## Supplier comparison

Supplier row identity uses printed name and batch. Report links are compared as sets, so reordering rows or moving a report between columns does not create a new report. New batches/products, removed rows and changed report links are recorded. An empty/malformed export cannot replace the baseline. Returning to older content remains a real change. Exact guide lot matches are suggestions for review only; ambiguous matches remain unmatched. External report links remain unverified until independently reviewed.

## Evidence and interactions

Literature discovery reads Europe PMC metadata/abstracts into a pending queue. Search coverage is a bounded exact-name title/abstract search, recorded as such; synonyms, blend ingredients, route-specific terms, full-text interactions and older studies need additional reviewed searches. It cannot establish that all relevant evidence or interactions have been found. Unresolved identities are blocked from medical discovery. Search errors cannot create a “no evidence” medical conclusion.

Example discovery/import commands:

```sh
python -m gloformulas.cli research-discover PRODUCT_ID --limit 100
python -m gloformulas.cli research-import PRODUCT_ID work/articles.json
python -m gloformulas.cli research-status
```

A medical claim must be separately entered from its actual primary source. Before approval, attach applicability/context:

```json
{
  "evidence_id": "REAL_EVIDENCE_ID",
  "population": "Population actually studied",
  "route": "Route actually studied",
  "species": "Species actually studied",
  "outcome": "Specific documented outcome",
  "locator": "Source section, page or table",
  "limitations": "Limits on applying this finding",
  "review_due": "2027-01-01",
  "source_candidate_id": "OPTIONAL_LINKED_CANDIDATE_ID"
}
```

```sh
python -m gloformulas.cli evidence-context work/context.json
python -m gloformulas.cli approve EVIDENCE_ID --reviewer 'Qualified reviewer' --quality human_trial --publication-date 2020-01-01
```

These are examples, not reviewed findings. Approved prose includes its population, species, route and limitations. A future publication date, expired review, retracted linked source, or missing context blocks approval/display. Editing context resets approval. Observed source metadata updates also return linked claims to pending. Observed retractions revoke linked approvals. Retraction detection is limited to records actually retrieved; it is not an exhaustive alert service. Inherited guide claims cannot be approved as primary evidence.

Interaction entries include the peptide, other agent, evidence, population, route, finding and review. Allowed relationships are a documented interaction, a potential precaution, or insufficient evidence. “Safe combination” is not an allowed relationship. Each reviewed entry requires an approved, currently reviewable claim in the relevant category. No automated search or COA result approves an interaction. “Insufficient evidence” must accurately describe the scope searched, rather than claim a universal absence of interaction risk.

Daily checks generate a private draft and evidence-gap queue; they never call the Google Docs publisher. The original Google Doc is unchanged until a separately reviewed update is ready.
