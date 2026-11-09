# Connect Google Docs through your browser

Publishing starts **disabled**. Merging this change does not turn it on. No passwords, Google account tokens or service-account keys belong in chat or repository files.

## What this release updates

The publisher maintains a marked **Verified product and COA updates** section at the end of the guide. It does not rebuild the document or rewrite the original 46 entries, medical prose, tables, headings or links. New listings and changed vial sizes appear here after a fresh, successful complete official catalog check. These are catalog facts, not verified medical identities. Each entry includes its source and observation date. Last known facts remain when a check fails. Discontinuation requires explicit primary-source evidence and manual approval; disappearance alone never suffices. Confirmed discontinuation is shown as a status, preserving the historical record.

COAs publish only after authenticated primary PDF retention, reviewed exact product/batch linkage, reviewed product identity, successful current verification rules and no associated open flags. Supplier/unverified reports never become GloFormulas-specific testing. A downloaded PDF alone is not enough. Medical statements, interactions and uncertain identities are not automatically published. Existing medical approval rules remain; this first publisher does not rewrite even approved medical prose. A later, individually mapped editing phase is needed to update the old tables themselves safely.

The test copy created during implementation has only an empty factual-updates section; no invented product data was inserted. Connector readback verified preservation of the preceding native content. This does **not** establish that GitHub's Google connection works or that the page layout has been visually approved.

## 1. Merge the publishing pull request

Merge through GitHub as usual. Keep `GLO_DOCS_MODE` absent or `disabled`. Leave `GLO_STATE_KEY` and the existing source configuration alone.

## 2. Create a Google publisher identity (no payment or terminal)

This uses Google's supported **Workload Identity Federation**. GitHub receives short-lived access for this repository's main-branch monitor only. There is no downloaded Google private key to protect or renew. The connection in ChatGPT cannot be reused by an unattended GitHub job.

1. Open [Google Cloud Console](https://console.cloud.google.com/). Sign in with your own Google account. Create a project named `GloFormulas Guide Publisher`. You do not need to enable billing for the APIs used here. If your organization forbids project creation, ask its administrator to create it; do not buy hosting.
2. In **APIs & Services → Library**, enable **Google Docs API**, **IAM Service Account Credentials API**, and **Security Token Service API**. Google may ask to enable IAM APIs while creating the identity pool; allow that within this project.
3. In **IAM & Admin → Service Accounts**, choose **Create service account**. Name it `glo-guide-publisher`. Skip granting it project roles. Finish. Copy its email address. Do **not** create a key or give it Owner/Editor access to the project.
4. In **IAM & Admin → Workload Identity Federation**, create a pool named `github-guide` and an **OpenID Connect (OIDC)** provider named `github`. Issuer URL: `https://token.actions.githubusercontent.com`. Use the default audience.
5. Set the attribute mappings below. Google's screen may already have `google.subject`; retain it.

   | Google attribute | OIDC claim |
   | --- | --- |
   | `google.subject` | `assertion.sub` |
   | `attribute.repository_id` | `assertion.repository_id` |
   | `attribute.repository_owner_id` | `assertion.repository_owner_id` |
   | `attribute.ref` | `assertion.ref` |

6. Set the provider's **attribute condition** exactly as follows. These numeric GitHub IDs pin the repository and owner; the workflow and main branch are also restricted.

   ```text
   assertion.repository_id == '1448597006' && assertion.repository_owner_id == '353467729' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref == 'Nrond4/glomformulas-peptide-reference/.github/workflows/daily-monitor.yml@refs/heads/main' && (assertion.event_name == 'schedule' || assertion.event_name == 'workflow_dispatch')
   ```

7. Select the pool and choose **Grant access → Grant access using Service Account impersonation**. Choose `glo-guide-publisher`. Restrict the principals to `attribute.repository_id` equal to `1448597006`. This grants **Workload Identity User** on that service account; do not grant Service Account Admin or project-wide roles. If the screen asks for a principal manually, its form is `principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-guide/attribute.repository_id/1448597006` (replace only `PROJECT_NUMBER` with your project's numeric number).
8. Open the provider details and copy its full resource name: `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-guide/providers/github`. Use the numeric project number, not the project name.

The application requests only `https://www.googleapis.com/auth/documents`. It does not request Drive-wide access, read email, change sharing or create files. Google Docs has no OAuth scope restricted to editing just one arbitrary existing document: the separate service account's document sharing is the resource boundary. Share only the intended test/live documents with it. An organization sharing policy can block service-account access; that needs the document owner's administrator, not a code workaround.

## 3. Share only the test copy and enter GitHub settings

1. Open the test copy linked in the implementation response. In **Share**, add the service-account email as **Editor**. Keep **General access → Restricted**. Uncheck notification if Google offers it. Do not share the original with this account yet. The native copy may inherit existing sharing; inspect its Share screen and restrict access as appropriate.
2. In GitHub open **Settings → Secrets and variables → Actions → Variables**. Add these **repository variables**:

   | Name | Value |
   | --- | --- |
   | `GLO_GOOGLE_WIF_PROVIDER` | Full provider resource name copied above |
   | `GLO_GOOGLE_SERVICE_ACCOUNT` | Publisher service-account email |
   | `GLO_DOCS_MODE` | `disabled` |

3. On the **Secrets** tab add **repository secrets** `GLO_DOCS_ORIGINAL_ID` and `GLO_DOCS_TEST_ID`. Each value is only the document ID between `/d/` and `/edit` in its URL. These are document identifiers, not your password or Google authorization token. Keep them out of public workflow inputs. Do not change `GLO_STATE_KEY`.

## 4. Run the GitHub test and inspect it

1. Open **Actions → GloFormulas Daily Monitor → Run workflow**. Select **main**.
2. Check **Test Docs publishing on the separate test copy only**. Leave initialization unchecked and review action `none`. Run it.
3. Open the run. The Google steps should authenticate, prepare a backup, upload the encrypted backup, apply any ready facts, and verify readback. Its job can still be red if unrelated source checks are incomplete: inspect the Google publishing steps individually.
4. The test copy may say that no new verified facts are ready. That is intentional if catalog checks remain rate-limited and COAs still need review. It tests the safe connection, not successful live source discovery.
5. Open the test copy. Check its original headings, links, tables and page layout, plus the new section at the end. Check **File → Version history → See version history**. Do not edit the marker lines. Manually editing publisher-owned text pauses later automated replacement once a verified receipt exists.
6. Run the same test again. It should not add another section or duplicate records. Check that both test runs saved encrypted state and backup artifacts. Save the second successful **Test/operation receipt** from the publishing step; it is an opaque hash, not a credential. A connector test outside GitHub is not an activation receipt.

## 5. Activate only after you have verified the test

1. Keep a native backup of the original: open it and choose **File → Make a copy**. Keep the copy private. This is also the easiest browser-accessible full-format backup.
2. Share the original with the same service-account email as **Editor**, keeping general access Restricted. Ensure the last paragraph is blank (add one at the end if necessary). This release initializes only a single-tab document automatically; multiple tabs require destination review.
3. Add repository variable `GLO_DOCS_TEST_VERIFIED` with the receipt from the latest successful GitHub test you personally inspected. The code checks that receipt against encrypted history, the test document ID and this publisher version. A random value or a successful PDF download cannot unlock publishing.
4. Set `GLO_DOCS_MODE` to `live`. Ensure the existing `GLO_MONITOR_ENABLED` variable is `true` if you want scheduled daily runs. The schedule remains 8:00 AM Mountain with daylight saving; GitHub may run later in its queue.
5. Run the workflow on **main** with the **test checkbox unchecked** and initialization unchecked. Inspect its Google steps and the original document. Keep the receipt and run link. Automatic live operation is only confirmed after this connection and a scheduled run have actually succeeded.

To keep testing every day instead, set mode `test` and do not share the original. To stop Google writes immediately, set mode `disabled` and remove the publisher account from the document's Share screen. Monitoring can continue.

## Manual review, history and recovery

Every publication uses Google's current revision ID. A concurrent human edit stops the write. The publisher replaces only its marked section, verifies the readback and existing native prefix, and records the result in the encrypted database. It stops on missing/duplicate markers, unsupported native objects inside that section, unknown write outcomes, or unexpected changes. No network write is retried automatically.

Before each write a complete native Docs API snapshot, proposed requests and sources are encrypted with `GLO_STATE_KEY` and uploaded as `glo-docs-backup-RUN-ATTEMPT`. GitHub stores these for **90 days**, not indefinitely. The JSON snapshot is an audit/reconstruction backup, not a one-click full-fidelity Google restore. Google's own document version history and your private native copy are the browser recovery route; the service account has no Drive permission to create backup copies. Keep longer-term encrypted copies if desired. Database history and facts travel in the existing encrypted state. No plaintext snapshot, private research, Google access token or credential file is uploaded.

For a confirmed discontinuation, put the following private proposal in a GitHub repository **secret** named `GLO_DOCS_REVIEW_JSON`. Use the exact catalog product key from the private database/report, and a real explicit discontinuation source you have inspected; never use a missing page or a failed check as evidence:

```json
{"kind":"removal","payload":{"product_key":"EXACT_KEY","source":"https://OFFICIAL-EXPLICIT-DISCONTINUATION-SOURCE","explicit_discontinuation":true}}
```

Run the manual workflow with review action **queue** (Google publishing can remain disabled). It prints only a proposal digest. After inspecting the underlying evidence, run again with **approve** and that exact digest in the review-digest field. GitHub records the initiating account as reviewer. Changing the private payload invalidates the approval digest. The next publication marks the product discontinued; it does not erase historical source material. Uncertain COA proposals cannot use this generic approval route: their identity/batch linkage must first be resolved in the lab verification engine. Medical claim and interaction approval remains the existing primary-source/context review process; this release offers no shortcut to publish those automatically.

If a write times out, the public log includes an opaque **Publication operation receipt** and the encrypted database marks it uncertain. First set mode `disabled`, inspect the document and Google's version history, and restore the previous version if needed. To unblock only an exact known state, temporarily choose the intended test/live mode, run the workflow with review action **reconcile**, and enter that uncertain operation receipt in the review-digest field (check the test box for a test-copy recovery). Recovery only succeeds if the unchanged prefix matches and the managed section exactly equals either the intended new section or its known prior state. It will not guess or discard unfamiliar text. If reconciliation fails, keep publishing disabled and retain the run link and backup for repair. Never reinitialize the database to clear an error.

## Remaining setup and limits

No Google federation identity is configured by merging code. No live automatic publishing is enabled by the test copy created in ChatGPT. A successful GitHub connection, visual inspection, duplicate test, receipt activation and original-document sharing are still required. Existing catalog rate limiting, unreviewed COA matches and absent literature configuration remain separate source issues; this publisher does not hide or fix them. Original rows are preserved, so they can differ from the newer dated facts in the maintained section until a separately reviewed field-mapping phase is implemented.

Official references: [Google GitHub federation instructions](https://cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines#github-actions), [Google authentication action](https://github.com/google-github-actions/auth), [Docs authorization scopes](https://developers.google.com/workspace/docs/api/auth), [Docs revision write control](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate), [Google Docs version history](https://support.google.com/docs/answer/190843).
