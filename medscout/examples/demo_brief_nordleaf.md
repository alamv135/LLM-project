# Investment Readiness Brief: Nordleaf Cardio — PulseWeave

Run `20261103-180107-54c338` · started 2026-11-03T18:01:07Z · model `mock/mock-scripted-policy` · data backend `mirror` · tools via `local`

> Preliminary, evidence-grounded first-pass screen for specialist review. Not investment, clinical, engineering or regulatory advice. Verdicts describe what the retrieved evidence does and does not establish.
> **SYNTHETIC DATA:** some evidence came from the offline synthetic mirror of a fictional case. It does not describe real FDA, registry or literature records.
> **MOCK MODEL:** decisions were produced by the scripted mock policy, not an LLM.

## Snapshot

| Verdict | Claims |
|---|---|
| Publicly supported | 4 |
| Partially supported | 2 |
| Possibly contradicted | 1 |
| Company-reported only | 5 |
| Unresolved | 0 |

12 claims extracted from 3 documents; 15 evidence items retained; 9 questions for specialist review.

## 1. Product evidence

### C1 · regulatory · Partially supported

**Claim (company-reported):** “PulseWeave is FDA approved for the detection of atrial fibrillation in adults.”  
_Source: pitch_deck_2026-04.md, section 'Slide 4 — Regulatory', lines 12-14_

**Assessment:** FDA lists a 510(k) clearance for PulseWeave Ambulatory ECG Patch with Arrhythmia Detection Software (K251234, 2025-02-20) [E2], but no PMA approval for the company.

**Evidence:**
- [E1] Company material: pitch_deck_2026-04.md, section 'Slide 4 — Regulatory', lines 12-14 _(company-reported)_
- [E2] openFDA 510(k): K251234 — Nordleaf Cardio Technologies, Inc., “PulseWeave Ambulatory ECG Patch with Arrhythmia Detection Software”, Substantially Equivalent 2025-02-20 _(SYNTHETIC)_

**Possible conflicts:**
- Claim says 'FDA approved'; the public record is a 510(k) clearance (K251234, 'Substantially Equivalent'). A 510(k) clearance is not a PMA approval.

**Entity match:** not confirmed by a reviewer.

**Open questions:**
- Confirm that FDA applicant 'Nordleaf Cardio Technologies, Inc.' is the same legal entity as 'Nordleaf Cardio'.

### C2 · regulatory · Company-reported only

**Claim (company-reported):** “A 510(k) submission for PulseWeave Pro, our 30-day next-generation patch, is planned for Q3 2026.”  
_Source: pitch_deck_2026-04.md, section 'Slide 4 — Regulatory', lines 12-14_

**Assessment:** The claim describes a planned submission; FDA databases only list decided submissions, so no public record can confirm it yet. The existing clearance K251234 covers the current device, not the planned one.

**Evidence:**
- [E1] Company material: pitch_deck_2026-04.md, section 'Slide 4 — Regulatory', lines 12-14 _(company-reported)_
- [E2] openFDA 510(k): K251234 — Nordleaf Cardio Technologies, Inc., “PulseWeave Ambulatory ECG Patch with Arrhythmia Detection Software”, Substantially Equivalent 2025-02-20 _(SYNTHETIC)_

**Open questions:**
- Request the planned submission timeline and any FDA pre-submission feedback.

### C3 · clinical · Possibly contradicted

**Claim (company-reported):** “Our pivotal multicenter PULSE-AF study completed enrollment of 1,200 patients in 2025.”  
_Source: pitch_deck_2026-04.md, section 'Slide 5 — Clinical evidence', lines 16-19_

**Assessment:** Registry record NCT09912345 [E4] differs from the claim.

**Evidence:**
- [E3] Company material: pitch_deck_2026-04.md, section 'Slide 5 — Clinical evidence', lines 16-19 _(company-reported)_
- [E4] ClinicalTrials.gov: NCT09912345 — “PulseWeave Patch for Atrial Fibrillation Detection (PULSE-AF)”, status RECRUITING, enrollment 600 (estimated), last update 2026-08-12 _(SYNTHETIC)_

**Possible conflicts:**
- Claim says the study completed enrollment; ClinicalTrials.gov NCT09912345 status is RECRUITING (last update 2026-08-12).
- Claim: 1,200 patients; registry enrollment: 600 (estimated).

**Open questions:**
- Ask the company for the current enrollment and status, and why the registry has not been updated.

### C4 · technical · Company-reported only

**Claim (company-reported):** “PulseWeave detects atrial fibrillation with 98% sensitivity.”  
_Source: pitch_deck_2026-04.md, section 'Slide 5 — Clinical evidence', lines 16-19_

**Assessment:** The sensitivity figure appears only in company materials. A related publication exists (PMID 99000001) [E5], but its reported figures are not available in the retrieved fields.

**Evidence:**
- [E3] Company material: pitch_deck_2026-04.md, section 'Slide 5 — Clinical evidence', lines 16-19 _(company-reported)_
- [E9] Company material: technical_whitepaper_v2.md, section 'Algorithm performance', lines 12-13 _(company-reported)_
- [E5] PubMed: PMID 99000001 — “Diagnostic accuracy of a wearable single-lead patch ECG algorithm for atrial fibrillation: a retrospective validation.”, Journal of Synthetic Cardiology 2025 Sep _(SYNTHETIC)_

**Possible conflicts:**
- Company materials report different sensitivity values: 98% (pitch_deck_2026-04.md, E3); 96.1% (technical_whitepaper_v2.md, E9)

**Open questions:**
- Obtain the validation report: which sensitivity figure is current, on what dataset and reference standard?

### C5 · clinical · Publicly supported

**Claim (company-reported):** “Our validation results were published in a peer-reviewed cardiology journal.”  
_Source: pitch_deck_2026-04.md, section 'Slide 5 — Clinical evidence', lines 16-19_

**Assessment:** A matching publication exists: PMID 99000001, Journal of Synthetic Cardiology 2025 Sep [E5]. Study design and reported figures were not assessed.

**Evidence:**
- [E3] Company material: pitch_deck_2026-04.md, section 'Slide 5 — Clinical evidence', lines 16-19 _(company-reported)_
- [E5] PubMed: PMID 99000001 — “Diagnostic accuracy of a wearable single-lead patch ECG algorithm for atrial fibrillation: a retrospective validation.”, Journal of Synthetic Cardiology 2025 Sep _(SYNTHETIC)_

**Open questions:**
- Have a clinical specialist review the study design and endpoints.

### C9 · regulatory · Publicly supported

**Claim (company-reported):** “Nordleaf Cardio Technologies, Inc. announced today that the U.S. Food and Drug Administration has cleared PulseWeave, a wearable ECG patch with automated arrhythmia detection.”  
_Source: press_release_2025-02-27.md, section 'Press release', lines 2-7_

**Assessment:** FDA 510(k) record K251234 for PulseWeave Ambulatory ECG Patch with Arrhythmia Detection Software (2025-02-20) [E2] matches the claimed regulatory status.

**Evidence:**
- [E6] Company material: press_release_2025-02-27.md, section 'Press release', lines 2-7 _(company-reported)_
- [E2] openFDA 510(k): K251234 — Nordleaf Cardio Technologies, Inc., “PulseWeave Ambulatory ECG Patch with Arrhythmia Detection Software”, Substantially Equivalent 2025-02-20 _(SYNTHETIC)_

**Entity match:** not confirmed by a reviewer.

**Open questions:**
- Confirm that FDA applicant 'Nordleaf Cardio Technologies, Inc.' is the same legal entity as 'Nordleaf Cardio'.

### C10 · regulatory · Publicly supported

**Claim (company-reported):** “PulseWeave received FDA 510(k) clearance (K251234) in February 2025.”  
_Source: technical_whitepaper_v2.md, section 'Regulatory status', lines 9-10_

**Assessment:** FDA 510(k) record K251234 for PulseWeave Ambulatory ECG Patch with Arrhythmia Detection Software (2025-02-20) [E2] matches the claimed regulatory status.

**Evidence:**
- [E7] Company material: technical_whitepaper_v2.md, section 'Regulatory status', lines 9-10 _(company-reported)_
- [E2] openFDA 510(k): K251234 — Nordleaf Cardio Technologies, Inc., “PulseWeave Ambulatory ECG Patch with Arrhythmia Detection Software”, Substantially Equivalent 2025-02-20 _(SYNTHETIC)_

**Entity match:** not confirmed by a reviewer.

**Open questions:**
- Confirm that FDA applicant 'Nordleaf Cardio Technologies, Inc.' is the same legal entity as 'Nordleaf Cardio'.

### C11 · technical · Company-reported only

**Claim (company-reported):** “In a retrospective validation on 2,400 annotated recordings, the algorithm achieved 96.1% sensitivity and 94.0% specificity for atrial fibrillation.”  
_Source: technical_whitepaper_v2.md, section 'Algorithm performance', lines 12-13_

**Assessment:** The sensitivity figure appears only in company materials. A related publication exists (PMID 99000001) [E5], but its reported figures are not available in the retrieved fields.

**Evidence:**
- [E9] Company material: technical_whitepaper_v2.md, section 'Algorithm performance', lines 12-13 _(company-reported)_
- [E3] Company material: pitch_deck_2026-04.md, section 'Slide 5 — Clinical evidence', lines 16-19 _(company-reported)_
- [E5] PubMed: PMID 99000001 — “Diagnostic accuracy of a wearable single-lead patch ECG algorithm for atrial fibrillation: a retrospective validation.”, Journal of Synthetic Cardiology 2025 Sep _(SYNTHETIC)_

**Possible conflicts:**
- Company materials report different sensitivity values: 98% (pitch_deck_2026-04.md, E3); 96.1% (technical_whitepaper_v2.md, E9)

**Open questions:**
- Obtain the validation report: which sensitivity figure is current, on what dataset and reference standard?

### C12 · clinical · Publicly supported

**Claim (company-reported):** “The PULSE-AF trial is enrolling up to 600 participants at 12 sites.”  
_Source: technical_whitepaper_v2.md, section 'Ongoing research', lines 15-15_

**Assessment:** Registry record NCT09912345 [E4] is consistent with the claim.

**Evidence:**
- [E8] Company material: technical_whitepaper_v2.md, section 'Ongoing research', lines 15-15 _(company-reported)_
- [E4] ClinicalTrials.gov: NCT09912345 — “PulseWeave Patch for Atrial Fibrillation Detection (PULSE-AF)”, status RECRUITING, enrollment 600 (estimated), last update 2026-08-12 _(SYNTHETIC)_

## 2. Competitive and public commercial context

### C6 · competitive · Partially supported

**Claim (company-reported):** “Our main competitors are Corvane Diagnostics and Ashby Monitoring.”  
_Source: pitch_deck_2026-04.md, section 'Slide 6 — Market and competition', lines 21-23_

**Assessment:** FDA lists 2 other device holder(s) under product code ZXA; named competitors found there: Corvane Diagnostics. Shared product codes indicate comparable devices, not market share.

**Evidence:**
- [E10] Company material: pitch_deck_2026-04.md, section 'Slide 6 — Market and competition', lines 21-23 _(company-reported)_
- [E12] openFDA 510(k): K233456 — Corvane Diagnostics LLC, “Corvane Strip Wearable ECG Monitor”, Substantially Equivalent 2023-11-08 _(SYNTHETIC)_
- [E11] openFDA 510(k): K240789 — Ellery Health Inc., “Ellery LoopPatch ECG Recorder”, Substantially Equivalent 2024-06-14 _(SYNTHETIC)_
- [E13] openFDA classification: product code ZXA — Electrocardiograph, ambulatory, with arrhythmia detection (synthetic), class 2 _(SYNTHETIC)_

**Open questions:**
- No FDA record under product code ZXA for: Ashby Monitoring. Are they US-market competitors, and under which product code?
- Same product-code device holders not named by the company: Ellery Health Inc. (K240789). Are they competitors?

### C7 · commercial · Company-reported only

**Claim (company-reported):** “PulseWeave is reimbursed by Medicare nationwide.”  
_Source: pitch_deck_2026-04.md, section 'Slide 7 — Commercial traction', lines 25-27_

**Assessment:** Only company materials make this commercial claim; the integrated public sources cannot establish it.

**Evidence:**
- [E14] Company material: pitch_deck_2026-04.md, section 'Slide 7 — Commercial traction', lines 25-27 _(company-reported)_

**Open questions:**
- Verify coverage and coding with the CMS Medicare Coverage Database / MAC policies (source not integrated in this MVP). FDA authorisation does not establish coverage.

### C8 · commercial · Company-reported only

**Claim (company-reported):** “Over 40 hospitals have signed purchasing contracts with Nordleaf Cardio.”  
_Source: pitch_deck_2026-04.md, section 'Slide 7 — Commercial traction', lines 25-27_

**Assessment:** Only company materials make this commercial claim; the integrated public sources cannot establish it.

**Evidence:**
- [E14] Company material: pitch_deck_2026-04.md, section 'Slide 7 — Commercial traction', lines 25-27 _(company-reported)_

**Open questions:**
- Request customer contracts or revenue evidence; public sources rarely establish commercial traction.

## 3. Cross-document consistency

- **sensitivity** differs across company materials: 98% in pitch_deck_2026-04.md (D1#5); 96.1% in technical_whitepaper_v2.md (D3#4)

## 4. Questions requiring specialist attention

1. (C1, C9, C10) Confirm that FDA applicant 'Nordleaf Cardio Technologies, Inc.' is the same legal entity as 'Nordleaf Cardio'.
2. (C2) Request the planned submission timeline and any FDA pre-submission feedback.
3. (C3) Ask the company for the current enrollment and status, and why the registry has not been updated.
4. (C5) Have a clinical specialist review the study design and endpoints.
5. (C4, C11) Obtain the validation report: which sensitivity figure is current, on what dataset and reference standard?
6. (C6) No FDA record under product code ZXA for: Ashby Monitoring. Are they US-market competitors, and under which product code?
7. (C6) Same product-code device holders not named by the company: Ellery Health Inc. (K240789). Are they competitors?
8. (C7) Verify coverage and coding with the CMS Medicare Coverage Database / MAC policies (source not integrated in this MVP). FDA authorisation does not establish coverage.
9. (C8) Request customer contracts or revenue evidence; public sources rarely establish commercial traction.

## Source log

| ID | Source | Identifier | Retrieved | Independent | Synthetic | Link / location |
|---|---|---|---|---|---|---|
| E1 | Company material | D1#4 | 2026-11-03T18:01:07Z | no | no | file://pitch_deck_2026-04.md#section 'Slide 4 — Regulatory', lines 12-14 |
| E2 | openFDA 510(k) | K251234 | 2026-11-03T18:01:07Z | yes | yes | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID=K251234 |
| E3 | Company material | D1#5 | 2026-11-03T18:01:07Z | no | no | file://pitch_deck_2026-04.md#section 'Slide 5 — Clinical evidence', lines 16-19 |
| E4 | ClinicalTrials.gov | NCT09912345 | 2026-11-03T18:01:07Z | yes | yes | https://clinicaltrials.gov/study/NCT09912345 |
| E5 | PubMed | 99000001 | 2026-11-03T18:01:07Z | yes | yes | https://pubmed.ncbi.nlm.nih.gov/99000001/ |
| E6 | Company material | D2#1 | 2026-11-03T18:01:07Z | no | no | file://press_release_2025-02-27.md#section 'Press release', lines 2-7 |
| E7 | Company material | D3#3 | 2026-11-03T18:01:07Z | no | no | file://technical_whitepaper_v2.md#section 'Regulatory status', lines 9-10 |
| E8 | Company material | D3#5 | 2026-11-03T18:01:07Z | no | no | file://technical_whitepaper_v2.md#section 'Ongoing research', lines 15-15 |
| E9 | Company material | D3#4 | 2026-11-03T18:01:07Z | no | no | file://technical_whitepaper_v2.md#section 'Algorithm performance', lines 12-13 |
| E10 | Company material | D1#6 | 2026-11-03T18:01:07Z | no | no | file://pitch_deck_2026-04.md#section 'Slide 6 — Market and competition', lines 21-23 |
| E11 | openFDA 510(k) | K240789 | 2026-11-03T18:01:07Z | yes | yes | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID=K240789 |
| E12 | openFDA 510(k) | K233456 | 2026-11-03T18:01:07Z | yes | yes | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID=K233456 |
| E13 | openFDA classification | ZXA | 2026-11-03T18:01:07Z | yes | yes | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?id=ZXA |
| E14 | Company material | D1#7 | 2026-11-03T18:01:08Z | no | no | file://pitch_deck_2026-04.md#section 'Slide 7 — Commercial traction', lines 25-27 |
| E15 | Company material | D1#3 | 2026-11-03T18:01:08Z | no | no | file://pitch_deck_2026-04.md#section 'Slide 3 — Our product', lines 9-10 |

## Run log

- Model calls: 33 · tool calls: 18 (errors: 0) · format repairs: 0 · verification rejections: 0
- Tokens in/out: 73268/3809 (estimated) · estimated cost: $0.0000
- Model latency p50/p95: 0.1/0.4 ms · tool latency p50/p95: 6.0/11.5 ms · wall time: 0.48 s
- Tasks: T1 [regulatory-check] done (finished); T2 [regulatory-check] done (finished); T3 [clinical-evidence] done (finished); T4 [clinical-evidence] done (finished); T5 [regulatory-check] done (finished); T6 [regulatory-check] done (finished); T7 [clinical-evidence] done (finished); T8 [clinical-evidence] done (finished); T9 [competitive-context] done (finished); T10 [commercial-signals] done (finished); T11 [commercial-signals] done (finished); T12 [clinical-evidence] done (finished)
