---
name: claim-extraction
description: Identify material, checkable claims in company materials and keep their source locations.
---
# Procedure
1. Read each chunk. A claim is a statement an investor would want verified: regulatory status,
   clinical studies and publications, performance figures, product capabilities, named competitors,
   reimbursement, customers or other commercial traction.
2. Skip marketing language that cannot be checked ("best-in-class", "game-changing") and generic
   background about the disease or market.
3. Keep the wording close to the source. Preserve status language exactly ("FDA approved",
   "510(k) cleared", "submission planned", "completed enrollment") — the difference matters.
4. One claim per checkable statement. If two documents state different numbers for the same
   metric, keep both claims: the inconsistency is a finding.
5. Categories: regulatory | clinical | technical (performance figures) | product | competitive | commercial.
6. Materiality: high for regulatory status and pivotal clinical evidence; medium for performance,
   competition and commercial claims; low for minor product details.
7. Every claim must list the chunk ids it came from. No source location, no claim.
