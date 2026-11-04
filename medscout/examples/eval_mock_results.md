# MedScout evaluation 20261103-180108

provider `mock` · model `mock-scripted-policy` · backend `mirror` · temperature 0.0

> MOCK provider: results validate the pipeline only; they are not evidence of model quality.

## ordinary_nordleaf
_ordinary task_

| system | claim_recall | verdict_accuracy | false_resolution | evidence_support | conflict_recall | false_conflict | model_calls | tool_calls | tool_error_rate | format_repairs | verify_rejections | tokens_in | cost_usd | wall_time_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| medscout | 1.0 | 1.0 | 0.0 | 1.0 | 1.0 | 0.0 | 33 | 18 | 0.0 | 0 | 0 | 73268 | 0.0 | 0.41 |
| direct | 1.0 | 0.417 | 0.0 | 0.0 | 0.0 | 0.0 | 1 | 0 | 0.0 | 0 | 0 | 1206 | 0.0 | 0.01 |
| rag | 1.0 | 0.667 | 0.2 | 0.429 | 0.0 | 0.0 | 1 | 4 | 0.0 | 0 | 0 | 1547 | 0.0 | 0.03 |

Per-claim (MedScout):

- ✓ FDA approved: expected ['partially_supported'], got partially_supported
- ✓ PulseWeave Pro & planned: expected ['company_reported_only', 'unresolved'], got company_reported_only
- ✓ completed enrollment: expected ['possibly_contradicted'], got possibly_contradicted
- ✓ 98% sensitivity: expected ['company_reported_only', 'unresolved'], got company_reported_only
- ✓ peer-reviewed: expected ['publicly_supported', 'partially_supported'], got publicly_supported
- ✓ competitors: expected ['partially_supported'], got partially_supported
- ✓ Medicare: expected ['company_reported_only', 'unresolved'], got company_reported_only
- ✓ hospitals: expected ['company_reported_only', 'unresolved'], got company_reported_only
- ✓ has cleared PulseWeave: expected ['publicly_supported'], got publicly_supported
- ✓ K251234: expected ['publicly_supported'], got publicly_supported
- ✓ 96.1%: expected ['company_reported_only', 'unresolved'], got company_reported_only
- ✓ enrolling up to 600: expected ['publicly_supported'], got publicly_supported

## changed_request_regulatory_focus
_changed request (regulatory claims only)_

| system | claim_recall | verdict_accuracy | false_resolution | evidence_support | conflict_recall | false_conflict | model_calls | tool_calls | tool_error_rate | format_repairs | verify_rejections | tokens_in | cost_usd | wall_time_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| medscout | 1.0 | 1.0 | 0.0 | 1.0 | 1.0 | 0.0 | 16 | 6 | 0.0 | 0 | 0 | 36744 | 0.0 | 0.15 |

Per-claim (MedScout):

- ✓ FDA approved: expected ['partially_supported'], got partially_supported
- ✓ PulseWeave Pro & planned: expected ['company_reported_only', 'unresolved'], got company_reported_only
- ✓ has cleared PulseWeave: expected ['publicly_supported'], got publicly_supported
- ✓ K251234: expected ['publicly_supported'], got publicly_supported

## failure_quillon_tool_faults
_failure case: no public records + injected tool faults (510(k) timeout once, registry unavailable)_

| system | claim_recall | verdict_accuracy | false_resolution | evidence_support | conflict_recall | false_conflict | model_calls | tool_calls | tool_error_rate | format_repairs | verify_rejections | tokens_in | cost_usd | wall_time_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| medscout | 1.0 | 1.0 | 0.0 | – | – | 0.0 | 10 | 5 | 0.4 | 0 | 0 | 20578 | 0.0 | 0.45 |
| direct | 1.0 | 1.0 | 0.0 | – | – | 0.0 | 1 | 0 | 0.0 | 0 | 0 | 474 | 0.0 | 0.0 |
| rag | 1.0 | 1.0 | 0.0 | – | – | 0.0 | 1 | 4 | 0.25 | 0 | 0 | 479 | 0.0 | 0.66 |

Per-claim (MedScout):

- ✓ FDA clearance: expected ['company_reported_only', 'unresolved'], got company_reported_only
- ✓ 300-patient: expected ['unresolved', 'company_reported_only'], got unresolved
- ✓ insurers: expected ['company_reported_only', 'unresolved'], got company_reported_only

## robustness_nordleaf_malformed_output
_reliability: malformed model output, ungrounded citation, transient PubMed timeout_

| system | claim_recall | verdict_accuracy | false_resolution | evidence_support | conflict_recall | false_conflict | model_calls | tool_calls | tool_error_rate | format_repairs | verify_rejections | tokens_in | cost_usd | wall_time_s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| medscout | 1.0 | 1.0 | – | 1.0 | 1.0 | 0.0 | 35 | 18 | 0.0 | 1 | 1 | 78672 | 0.0 | 0.71 |
| direct | 1.0 | 0.0 | – | 0.0 | 0.0 | 0.0 | 1 | 0 | 0.0 | 0 | 0 | 1206 | 0.0 | 0.0 |
| rag | 1.0 | 0.333 | – | 0.333 | 0.0 | 0.0 | 1 | 4 | 0.0 | 0 | 0 | 1547 | 0.0 | 0.76 |

Per-claim (MedScout):

- ✓ FDA approved: expected ['partially_supported'], got partially_supported
- ✓ completed enrollment: expected ['possibly_contradicted'], got possibly_contradicted
- ✓ peer-reviewed: expected ['publicly_supported', 'partially_supported'], got publicly_supported
