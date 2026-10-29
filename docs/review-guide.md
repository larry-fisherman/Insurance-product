# Review the implemented product

Use `make full-up`, then visit http://localhost:3000/login and open a fictional example. The environment creates a separate agency each time; there is no shared demo password. Synthetic sessions last eight hours and disclose their MFA bypass. To test normal access, register a fictional email, enroll an authenticator, and save recovery codes.

## Workflow checks

- **Agency dashboard:** active submissions, open discrepancies, approved/exported submissions, failures, and upcoming due dates. Counts reflect the loaded local pipeline (up to 100 rows), not enterprise-wide analytics.
- **Insured accounts:** create/edit legal name, DBA, entity, address, operations, external reference; search; view submission/renewal history. Directory queries are capped and expose a cursor in the API.
- **Submission:** set proposed policy dates, required loss history, due date, responsibility, and renewal lineage. Creating a renewal retains the prior submission link; it does not carry forward approval or silently copy prior exposures.
- **Documents:** PDF/XLSX uploads, scan quarantine, classification, native parsing/OCR, status/error codes, retries, immutable replacement versions, exclusions with reasons, and original downloads after scanning. A stale excluded document remains in history.
- **Review:** source blocks display page/line or sheet/range. Exposure context includes basis, role, period, unit, currency, and scope. Nothing is selected in a new material conflict. Approve/correct/omit/reject with a reason. Add broker attestations when information was confirmed outside documents.
- **Missing information:** name, operations, and at least one contextual exposure are required. Every extracted group needs review. Missing coverage/loss requirements may be accepted by an approver with a reason and are included in exports. Missing loss runs never become a no-loss history.
- **Packages:** generate original PDF/XLSX/ZIP; inspect preview, revision, and manifest; approve the exact bytes; export manually. Changing evidence, facts, or material submission settings requires a new package and approval. ZIP includes original supporting files and a snapshot/manifest.
- **Follow-ups:** create and complete submission tasks; agency queue links back to their submission. No external email is sent.
- **Settings:** administrators invite users using expiring manually shared links and assign explicit role bundles. Administrator alone does not provide document/approval access. Last active administrator cannot be removed. Current permissions are checked again on requests.
- **History and measurement:** immutable audit entries record review/approval/export actions without full document text; usage captures processed pages and package generation. Broker-reported baseline/active preparation minutes support pilot time-savings measurements.

## Important limitations to challenge during review

The parser recognizes explicit labels and simple two-column schedules. Complex schedules, carrier-specific formats, repeated locations/classes, endorsements with operative wording, claims-made variants, multi-entity relationships, and intricate loss reports need a representative authorized corpus and insurance-domain review. The generic model does not infer carrier class mappings or classify insurance appetite.

Source snippets are bounded to 8,000 characters per line/row; inspect the full original for long wording. Parsed documents have a 2,000,000-character text budget and the local extractor allows at most 500 proposed facts per document. Oversized or unsupported inputs require splitting or manual handling. OCR citations identify the page and recognized text line, not a verified pixel-level bounding box. Original files are available for comparison. An unclassified or fully abstained document requires explicit manual review and an approver-recorded exception before generation. This build does not guarantee extraction completeness; all proposals need review, and missing core fields block output. Add an attested field only after independently verifying it.

MVP exports are original summaries; they are not carrier-approved applications or ACORD forms. The software makes no legal or insurance coverage conclusions. External distribution is manual. Operator-managed local retention is not a customer-facing deletion policy.

The default parser performs no paid AI calls. Provider adapters are tested with simulated responses; real models and vendor data handling have not been evaluated. `store:false` is a request parameter, not an assertion about vendor retention.

Browser tests use real API, PostgreSQL, Redis, worker, and storage services with fictional fixtures. Regression evaluation uses generated explicit-label packets and must not be cited as real-world insurance-document accuracy. See pilot gates before using any customer information.
