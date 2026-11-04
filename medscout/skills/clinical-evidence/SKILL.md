---
name: clinical-evidence
description: Check clinical-study, publication and performance-figure claims against ClinicalTrials.gov, PubMed and the company's own documents.
---
# Procedure
1. Classify the claim: trial status/enrollment, publication, or performance figure (sensitivity,
   specificity, accuracy).
2. Trials: search clinicaltrials_search by NCT id if stated, otherwise by sponsor (company name).
   No result? Try the product name as intervention once.
   Compare: overall_status (RECRUITING / ACTIVE_NOT_RECRUITING / COMPLETED ...), enrollment and
   whether it is ACTUAL or ESTIMATED, completion dates, last update date.
   "Completed enrollment" with a RECRUITING registry entry is a possible contradiction — note that
   registries can lag, so phrase it as a question for the company.
3. Publications: search pubmed_search with product + validation terms. A matching publication
   supports "published"; it does not by itself confirm the figures — say what was not assessed.
4. Performance figures: search_company_docs for the metric to see whether materials agree.
   Different numbers across documents are a conflict to report, even if neither is independent.
5. Never upgrade a figure to "publicly supported" unless an independent record states it.
