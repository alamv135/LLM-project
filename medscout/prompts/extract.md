PHASE: extract_claims
COMPANY: {{COMPANY}}
PRODUCT: {{PRODUCT}}

{{SKILL}}

Extract the material, checkable claims from the company material chunks below.
Return ONLY: {"claims":[{"text":"...","category":"regulatory|clinical|technical|product|competitive|commercial",
"source_chunk_ids":["D1#2"],"product":"...","status_language":"...","materiality":"high|medium|low"}]}
Use only chunk ids that appear below. Quote or closely paraphrase the claim; do not add facts.

CHUNKS_JSON:
{{CHUNKS_JSON}}
END_CHUNKS
