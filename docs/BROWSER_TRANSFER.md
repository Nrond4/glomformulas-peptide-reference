# Browser-only transfer of the existing 46-record database

You do not need a terminal, a download/upload of the raw database, a new service, or another secret. Keep the `GLO_STATE_KEY` you already added to GitHub. Do not send it to Codex or paste it into any workflow input.

## Your steps

1. **Merge the browser-transfer pull request into `main`.** This adds the temporary workflow **Secure Browser Database Transfer**.
2. Open **Actions → Secure Browser Database Transfer → Run workflow**. Choose branch **main**, choose **Prepare transfer**, leave Session blank, and click **Run workflow**.
3. Wait for a successful run, then **send Codex the run's browser link**. That link is not a secret. Do not send the key or download the database. Codex will verify the public-key fingerprint from that run, encrypt the existing private database in its workspace, and upload only the encrypted transfer on a separate `seed-transfer/…` data branch.
4. When Codex confirms the encrypted upload, open the same workflow again. Choose **Finish transfer** and enter the Session ID from the successful Prepare summary (Codex can repeat this non-secret ID for you). Click **Run workflow**.
5. When Finish succeeds, **the `monitor-seed` release already contains `monitor-seed.enc`**. Nothing else needs to be attached manually. Open **GloFormulas Daily Monitor → Run workflow** on `main`, check first-run initialization, and test. Its subsequent runs use the carried-forward encrypted database.

After successful initialization, you may disable **Secure Browser Database Transfer** through that workflow's Actions menu. Keep the daily monitor and `GLO_STATE_KEY`. Setting `GLO_MONITOR_ENABLED=true` enables the daily schedule after the manual tests, as described in [the monitoring guide](GITHUB_ACTIONS.md).

If Prepare or Finish fails, send Codex the run link and the failed step's name—not the encryption key. A transfer session lasts seven days. If it expires, start a new Prepare run; Codex must encrypt for the new session. Do not use an empty database as a substitute. The original private workspace must remain available until the protected seed is verified and published.

## How your data stays private

GitHub Actions can read your repository secret; this cloud workspace cannot. Sending an unencrypted database to Actions would expose it in a public repository, so this method does not do that.

Prepare creates a temporary encryption key pair inside Actions. The **public** half is safe for Codex to read. The **private** half is immediately encrypted using your existing `GLO_STATE_KEY`; only encrypted private-key bytes are uploaded to a temporary release. The unencrypted private key is not written to disk or displayed. Releases contain only the public recipient description and its encrypted private half.

Codex encrypts the real SQLite snapshot and approved saved source files using a fresh random AES key, then protects that AES key with the public recipient (RSA-OAEP SHA-256, 3072-bit key). The ciphertext is authenticated to the repository, session and recipient fingerprint. Codex never receives your repository secret or the private recipient key. Only the encrypted envelope is committed on the isolated transfer branch; neither the raw database nor plaintext research is committed, and `main` is not changed by that upload.

Finish runs trusted code from `main`, reads the transfer branch as data without executing its code, decrypts it inside the temporary Actions runner, and checks **all 46 products**, their logical product digest, the original database byte digest, database integrity, and every table's row count. It then uses the existing project encryption tool to create the standard `monitor-seed.enc`, restores it again for verification, and publishes only that encrypted seed. An existing seed asset is never overwritten silently.

No plaintext database, unencrypted key, source snapshot, credential, Google Doc update, or unverified medical publication is uploaded. The temporary workflow has repository Contents-write permission, which its steps use only to publish the public-key and encrypted-seed Release assets. It runs only by manual dispatch from `main`, with no pull-request trigger.

The bridge has been tested locally with the real private 46-record database using a disposable test key, without retaining or uploading the test transfer. It still requires your first Prepare click before Codex can perform the real encrypted upload.

## Codex continuation checklist — no user terminal required

When the user supplies the successful Prepare run link:

1. Read the run/job through the GitHub connector. Confirm repository `Nrond4/glomformulas-peptide-reference`, workflow `.github/workflows/seed-transfer.yml`, branch `main`, a successful `workflow_dispatch` Prepare run, and its session `<run_id>-<attempt>`.
2. Read the **public-key fingerprint** from the Prepare job log, not from a user-supplied arbitrary URL. It is intentionally public. Never request or fetch `GLO_STATE_KEY`.
3. Download only `transfer-public.json` from the corresponding `glo-transfer-<session>` release over the authorized HTTPS connection. Do not download `transfer-private.enc` into the cloud workspace. Validate repository/session, dates and the logged fingerprint; a substituted, expired or mismatched recipient must stop the upload.
4. Confirm the original private `work/reference.sqlite` still contains 46 products and passes its read-only integrity check. The helper also validates this and preserves a consistent SQLite snapshot.
5. Run the existing helper **inside Codex**, not in the user's browser terminal:

   ```sh
   python -m gloformulas.browser_transfer encrypt --state work --public work/transfer-public.json --repository Nrond4/glomformulas-peptide-reference --session SESSION_FROM_PREPARE --fingerprint PUBLIC_FINGERPRINT_FROM_JOB_LOG --output work/database.glo-transfer.json
   python -m gloformulas.browser_transfer publish --transfer work/database.glo-transfer.json --repository Nrond4/glomformulas-peptide-reference --session SESSION_FROM_PREPARE --checkout .
   ```

   These arguments contain no secret. The publish helper uses an isolated Git index, verifies that only the ciphertext path changed, creates a data-only `seed-transfer/<session>` branch, and never stages the private work directory or alters `main`. It refuses to overwrite an existing session branch.
6. Verify the remote encrypted branch and give the user the Session ID for **Finish transfer**. After Finish, inspect its successful verification and the existence of the encrypted `monitor-seed.enc` asset. Do not claim daily monitoring is active until the manual and scheduled monitor runs are confirmed.

If the private workspace or its database disappears before this transfer completes, stop and recover the existing private backup. Never silently reinitialize the database or substitute a reconstructed/empty guide. The transfer cannot be completed merely by giving Actions a public Git clone: the private original is deliberately outside Git.
