# Fusion Structured Analysis Contract

> Historical filename note: this page used to describe a separate evidence
> curation pass. That pass was removed on 2026-06-19. The live contract is
> structured analysis plus unconditional raw answers.

## Pipeline

```text
panel providers -> analysis judge -> host author
```

The judge receives the question and every successful panel answer labelled by real
provider id. It returns strict JSON matching `Analysis`:

```json
{
  "consensus": [],
  "contradictions": [
    {
      "topic": "string",
      "positions": [{ "ids": ["provider-id"], "claim": "string" }]
    }
  ],
  "partial_coverage": [{ "ids": ["provider-id"], "point": "string" }],
  "unique_insights": [{ "id": "provider-id", "insight": "string" }],
  "blind_spots": [],
  "confidence_notes": "string"
}
```

The tool response includes:

- `question`: the original prompt, echoed unconditionally
- `panel[]`: per-provider status metadata
- `analysis`: parsed judge output, or `null` if the judge failed
- `raw_answers[]`: every successful raw panel answer
- `meta`: timing, ok/failed counts, analysis provider id, cost estimate, and
  degradation fields
- `text`: Markdown rendering
- `thread_id`: echoed from the request when a session call; `null` for one-shot
- `compacted`: `true` when the session history was trimmed to fit a provider's
  context window

## Prompt Rules

The judge must analyze, not author. It should preserve contradictions, minority
claims, uncertainty, caveats, assumptions, source limits, and blind spots. It must
attribute positions and unique insights to real provider ids.

If the judge returns malformed JSON or fails schema validation, Imladris records
`meta.judge_error` and still returns all successful raw answers.
