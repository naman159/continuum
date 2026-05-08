# Character Canonicalizer — Design

## Problem

The strict resolver in `extraction/resolver.py` only matches characters by exact name or pre-existing alias. When the LLM emits the same character under multiple forms within or across chapters ("Elizabeth Bennet", "Lizzy", "Eliza", "the master of Pemberley"), each form becomes a separate row. Manual deduplication doesn't scale.

The previous approach (`fuzz.ratio` ≥ 85%) silently merged distinct characters whose names differed only by honorific ("Mr. Bennet" / "Mrs. Bennet"). It was removed.

## Principle

**Merge only when the chapter text grammatically establishes the reference.** Apposition ("Mr. Darcy, the master of Pemberley"), unambiguous possessive ("Elizabeth's father"), or restated full name. The LLM must cite the grammatical anchor — no merges from "vibes".

When a merge happens, the new form is appended to the matched character's `aliases` array so subsequent chapters resolve it via exact match without another LLM call. The system gets cheaper and more accurate over time.

## Architecture

A new pipeline step, `CharacterCanonicalizer`, runs **after extraction** and **before persistence**. It takes:
- The chapter text
- The character names mentioned in the extraction output
- The current character roster (canonical name, aliases, description, last-known state)

It returns a mapping `{candidate_name → resolution}` where `resolution` is either `("existing", uuid)` or `("new", None)`. For each `existing` resolution, it appends the candidate name to that character's `aliases` array.

When the strict resolver in `_persist_extraction` later calls `resolve_character("the master of Pemberley")`, the alias lookup at `resolver.py:66` succeeds and returns the existing character ID. No code changes required in the strict resolver.

## Components

### `extraction/canonicalizer.py` — new module

```python
class CharacterCanonicalizer:
    def __init__(self, db, novel_id, *, use_mock: bool | None = None): ...

    def canonicalize(self, *, chapter_text: str, candidate_names: set[str]) -> None:
        # 1. Load roster (excluding names that already exact-match in roster)
        # 2. If no unresolved candidates, return
        # 3. Build resolution prompt with chapter_text + candidates + roster
        # 4. Call LLM (single call per chapter)
        # 5. Parse response
        # 6. For each existing-verdict with non-empty grammatical_anchor,
        #    append candidate name to that character's aliases array (if not already there)
```

### `extraction/prompts.py` — new builder

```python
def build_canonicalization_prompt(
    chapter_text: str,
    candidates: list[str],
    roster: list[dict],
) -> tuple[str, str]:  # (system, user)
```

### `pipeline.py` — integration

In `process_chapter`, between extraction and `_persist_extraction`:

```python
candidate_names = _collect_character_names(extracted)
canonicalizer = CharacterCanonicalizer(db, novel_id=novel_id, use_mock=use_mock_llm)
canonicalizer.canonicalize(chapter_text=raw_text, candidate_names=candidate_names)
```

`_collect_character_names` gathers names from `new_entities.characters[].name`, `entity_deltas[].character_name`, and `events[].involved_characters`.

## LLM Contract

**System prompt:** instruct the model to merge only on grammatical evidence in the chapter text, return JSON, and quote the anchor exactly.

**User prompt structure:**
```
Chapter text:
<chapter raw text, possibly truncated>

Existing characters in this novel:
- id: <uuid>, name: "<canonical>", aliases: [...]
  description: "..."
  last known: location=..., goals=..., emotional_state=...

Candidate names from this chapter to resolve:
- "Lizzy"
- "the master of Pemberley"
- "John"

For each candidate, return JSON:
{ "resolutions": [
    {
      "candidate": "<exact candidate string>",
      "verdict": "existing" | "new",
      "id": "<uuid>" | null,
      "grammatical_anchor": "<verbatim quote from chapter>" | null,
      "reasoning": "<short>"
    }
] }

Rules:
- Verdict "existing" requires both id AND grammatical_anchor.
- A grammatical_anchor must be a verbatim substring of the chapter text
  showing the reference (apposition, possessive, restated name).
- If grammatical evidence is absent, return "new". When in doubt, return "new".
```

## Defensive Behaviors

- LLM returns malformed JSON → log warning, write zero aliases, proceed. Strict resolver will create duplicates rather than wrong merges.
- `verdict == "existing"` but `grammatical_anchor` empty/missing → treat as `new` (no alias write).
- `grammatical_anchor` not actually a substring of chapter text → treat as `new` (the LLM is hallucinating evidence).
- Candidate already in target's aliases → no-op (no duplicate alias).
- LLM call raises → log, skip resolution, proceed.

## Schema

No changes. Writes only to `characters.aliases` (existing `TEXT[]` column).

## Mock Mode

When `use_mock=True`, `canonicalize()` is a no-op. Mock pipeline tests still work; duplicates are created as before, which is fine for mock-mode validation.

## Testing

**Unit tests** (mock LLM response, no API calls):
1. Existing-verdict with valid grammatical_anchor → alias appended.
2. Existing-verdict with missing/empty anchor → no alias write.
3. Existing-verdict with anchor that's not in chapter text → no alias write.
4. Candidate already in aliases → no duplicate appended.
5. Malformed JSON response → no exception, no aliases written.
6. New-verdict → no roster modification.

**Integration test:** real Gemini call against Pride and Prejudice chapter 1. Expected: "Elizabeth Bennet" exists once with aliases including "Lizzy" / "Eliza"; "Mrs. Bennet" exists distinctly from "Mr. Bennet".

## Out of Scope

- Location, faction, object canonicalization (characters first; same pattern can be applied later).
- Embedding-based candidate pre-filtering (only needed if roster grows past LLM context limit).
- Resolution of pronouns ("she", "he") — extraction prompt's job, not resolver's.
