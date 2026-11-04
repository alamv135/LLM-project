---
name: competitive-context
description: Map comparable devices and named competitors using FDA product codes; report public competitive context without inferring market share.
---
# Procedure
1. Find the company's own FDA record (fda_510k_search by applicant) to get its product code.
2. List devices with the same product code (fda_510k_search by product_code, limit 10) and look up
   the code (fda_classification_lookup).
3. Compare with the competitors the company names: which appear under the same code, which do not,
   and which same-code device holders the company did not mention.
4. Verdict: named competitors confirmed and no notable omissions -> publicly_supported; some
   confirmed -> partially_supported; none found -> company_reported_only.
5. Public records show who holds comparable clearances; they do not show sales, pricing, adoption
   or market share. Put those as open questions.
