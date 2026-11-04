---
name: regulatory-check
description: Verify FDA regulatory-status claims (510(k), De Novo, PMA) against openFDA records and check the wording.
---
# Procedure
1. Identify the claimed status and its exact wording: cleared / approved / granted / submitted / planned.
2. Search fda_510k_search by applicant (the company name). If the claim mentions "approved",
   also search fda_pma_search. If a K-number or PMA number is stated, look it up directly.
3. No match? Try ONE variant: the product name as device_name, or a known alias. Do not loop.
4. Entity check: the FDA applicant name often differs from the brand name (legal suffixes,
   parent companies). Different companies can share a word ("Nordleaf Cardio" vs "Nordleaf Surgical").
   Match on the full company name and on the product/device name. If the match is plausible but not
   certain and it decides the verdict, ask the user once, with the exact applicant name as alias_candidate.
5. Compare wording with pathway:
   - 510(k) record -> "cleared" (substantially equivalent to a predicate). Calling it "FDA approved"
     is inaccurate -> partially_supported with the conflict stated.
   - PMA record -> "approved". De Novo -> "granted".
   - A planned or pending submission has no public decision record -> company_reported_only,
     with an open question about timeline / pre-submission feedback.
6. Check the decision date and the device described: a clearance for an earlier version does not
   cover a new version or a new indication.
7. Cite the FDA record's evidence id and the company excerpt's id.

For definitions, read_skill_reference("regulatory-check", "references/fda-pathways.md").
