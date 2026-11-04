PHASE: plan
COMPANY: {{COMPANY}}
PRODUCT: {{PRODUCT}}
FOCUS: {{FOCUS}}
AVAILABLE_SKILLS: {{SKILLS}}
MAX_TASKS: {{MAX_TASKS}}
PAST_CASES (earlier runs for this company, may be outdated): {{PAST_CASES}}

Plan the verification. Create research tasks for the claims below, most material first
(priority 1 = highest). A task may group claims that need the same evidence (for example the
same trial). Every claim must belong to exactly one task. Pick the skill that fits each task and
write a specific research question.
Return ONLY: {"tasks":[{"claim_ids":["C1"],"question":"...","skill":"regulatory-check","priority":1}]}

CLAIMS_JSON:
{{CLAIMS_JSON}}
END_CLAIMS
