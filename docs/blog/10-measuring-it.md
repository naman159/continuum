# Part 10 — How Do You Know It Works?

*Part 10 of the Continuum series. Nine posts built a system. This post is about the
uncomfortable question of whether any of it does what it claims — and about how easy it
is to build a measurement that certifies a broken system as working.*

---

## The two kinds of correctness

There are 419 tests across 72 test files in this codebase. Running them takes about
twenty seconds and produces `416 passed, 3 skipped` — the three skips are the real-LLM
evals, which are gated behind an environment variable so they don't spend API credits by
default. Consider what the 416 actually prove.

Take a resolver test:

```python
def test_partial_name_refuses_ambiguous_match():
    # two characters both matching "Elizabeth"
    ...
    assert resolver.resolve_character("Elizabeth", create=False) is None
```

That's a good test. Given this input, the function produces this output. Deterministic,
fast, reliable. It will catch a regression forever.

Now try to write the test we actually want:

```python
def test_extraction_finds_the_characters():
    result = extractor.extract_chapter(chunks, context)
    assert result["new_entities"]["characters"] == ???
```

What goes in `???`

You can't say. Run it twice and you might get "Mira Solen" once and "Mira" the next.
You'll get four characters, or five if the model decides the porter counts. Change the
model version and everything shifts. There is no single correct output — there's a
*distribution* of acceptable outputs.

This is the fundamental split in any system with an LLM inside it:

```
┌──────────────────────────────────────────────────────────────────────┐
│  DETERMINISTIC                     │  STATISTICAL                    │
│  ─────────────                     │  ───────────                    │
│  dedup, resolution, replay,        │  extraction accuracy,           │
│  materialization, cutoff filtering,│  entity resolution quality,     │
│  interval maths, merge repair      │  retrieval relevance,           │
│                                    │  critic precision/recall        │
│                                    │                                 │
│  Same input → same output          │  Same input → varying output    │
│  Correctness is exact              │  Correctness is a rate          │
│                                    │                                 │
│  → unit tests                      │  → evals                        │
└──────────────────────────────────────────────────────────────────────┘
```

An **eval** is a test for the right column. Instead of "does this function return exactly
X", it asks "across a set of cases with known-good answers, what fraction does the system
get right, and is that fraction above the bar?"

The key structural difference: an eval needs **ground truth** — a dataset where someone
has written down the correct answers by hand.

---

## Building the golden dataset

The obvious thing is to grab a real novel and label it. Public-domain *Pride and
Prejudice* is right there.

Bad idea, for three reasons.

**Labelling is enormous.** Every character, every alias, every possession interval,
every knowledge acquisition, across 61 chapters. Weeks of work, and it's wrong in places
you won't find.

**It's in the training data.** Every model has read *Pride and Prejudice*. Ask it who
"Lizzy" is and it answers from memory, not from your chapter. You'd be measuring
recall-of-training-data, and it would look fantastic right up until you pointed the
system at an unpublished manuscript.

**It's not adversarial where you need it.** Real novels don't reliably contain the exact
edge cases you want tested — an object changing hands five times, a character referred to
only by description.

So Continuum has a hand-written fixture: **"The Lantern of Veyra"**, ten chapters,
roughly 400 characters each. Here's chapter 1, entire:

> *Mira Solen had worked the tide-tables at the Harbor of Veyra since she was twelve, and
> she knew every crate that came off the ferries. The one that split open on the quay
> that morning was not on any manifest. Inside, wrapped in oilcloth, lay a brass lantern,
> cold to the touch and heavier than it had any right to be. Mira looked around the empty
> quay, then tucked the Brass Lantern into her satchel and walked home along the seawall.*

Look at what's engineered into 90 words:

- A character introduced by full name (**Mira Solen**), later called **Mira** and
  **harbor-girl**.
- A location introduced by full name (**Harbor of Veyra**), later called **the harbor**.
- An object introduced in lower case (**a brass lantern**) and then capitalised (**the
  Brass Lantern**) — a real casing trap.
- An unambiguous possession gain, with an explicit actor.

Every sentence is doing test-fixture work while still reading like prose.

The whole novel is built around one spine: **the lantern changes hands five times.**

```
ch1   Mira finds it on the quay                        Mira      ●━━━━━┓
ch2   Mira takes it to the Glass Archive                             ┃
ch3   Toren explains it opens the Undervault                         ┃
ch4   Mira gives it to Toren to study                    Toren    ●━━┛
ch5   Kessa steals it from the iron cabinet              Kessa    ●━━┓
ch6   pursuit through the Saltmarsh                                  ┃
ch7   Kessa falls; Odo the ferryman fishes it out        Odo      ●━━┛
ch8   Odo returns it to Mira                             Mira     ●━━━━━━━▶
ch9   Mira lights it at the sealed gate                                 (still holds)
ch10  the Ash Council bargains in the Undervault
```

That single thread exercises almost everything: interval opening and closing,
supersession, replay ordering, the reference-only resolver, cutoff filtering. If any of
Post 5's machinery is broken, this fixture catches it.

### The answer key

A YAML file with the correct answers, hand-written:

```yaml
# Interval semantics match possesses_edges: since_chapter/until_chapter are
# inclusive chapter numbers; until: null means "still held at end of novel".
entities:
  characters: [Mira Solen, Toren Vale, Kessa Dray, Odo Bram]
  locations:  [Harbor of Veyra, Glass Archive, Saltmarsh, Undervault]
  objects:    [Brass Lantern]
  factions:   [Ash Council]

possessions:
  - {object: Brass Lantern, holder: Mira Solen, since: 1, until: 3}
  - {object: Brass Lantern, holder: Toren Vale, since: 4, until: 4}
  - {object: Brass Lantern, holder: Kessa Dray, since: 5, until: 6}
  - {object: Brass Lantern, holder: Odo Bram,   since: 7, until: 7}
  - {object: Brass Lantern, holder: Mira Solen, since: 8, until: null}

knowledge:
  - {character: Mira Solen, fact: the brass lantern opens the undervault, since: 3}
  - {character: Mira Solen, fact: kessa dray serves the ash council,      since: 8}

queries:
  - {text: who found the brass lantern at the harbor, expect_chapters: [1]}
  - {text: Kessa Dray steals the lantern from the iron cabinet, expect_chapters: [5]}
  - {text: ferryman fishes the lantern out of the channel, expect_chapters: [7]}
  # ...ten in total, one per chapter
```

Note the comment on line 1. Inclusive-vs-exclusive interval semantics are exactly the
kind of thing where the fixture author and the implementer can silently disagree and
produce an eval that measures a misunderstanding. Pin it in the data.

And the resolution ground truth, which is where the fixture earns its keep:

```yaml
# Ground truth for entity resolution. Every surface form below actually occurs
# in the chapter text; `canonical` is the entity it refers to. Surfaces are
# graded pairwise (see scoring.pairwise_resolution), so this doubles as the
# negative set: any two surfaces under different canonicals must NOT be merged.
#
# The descriptive surfaces ("the thief", "the old ferryman") are the point.
# Lexical matching cannot reach them, and they are also the ones extraction
# most often drops entirely — which is why the eval reports coverage apart
# from precision/recall rather than scoring a dropped surface as a merge miss.
resolution:
  - canonical: Mira Solen
    type: character
    surfaces: [Mira Solen, Mira, harbor-girl]
  - canonical: Toren Vale
    type: character
    surfaces: [Toren Vale, Toren, the archivist on duty]
  - canonical: Kessa Dray
    type: character
    surfaces: [Kessa Dray, the woman who called herself Kessa Dray, the thief]
  - canonical: Odo Bram
    type: character
    surfaces: [Odo Bram, Odo, the old ferryman]
  - canonical: Brass Lantern
    type: object
    surfaces: [Brass Lantern, brass lantern, the strange lantern, vault-key]
  - canonical: Harbor of Veyra
    type: location
    surfaces: [Harbor of Veyra, the harbor]
  # ...
```

Three design decisions in that block.

**"Every surface form below actually occurs in the chapter text."** Not invented. The
eval is unfalsifiable if the ground truth contains things the system was never shown.

**"This doubles as the negative set."** Pairwise scoring means every pair is a judgement.
Surfaces under *different* canonicals are automatically the pairs that must NOT be
merged. You get the negative cases for free by writing the positive ones — which
massively reduces the labelling burden and eliminates a whole class of "we only tested
the happy path" failure.

**"The descriptive surfaces are the point."** Recall the similarity numbers from Post 4:

```
0.133  'Kessa Dray'  vs  'the thief'
0.296  'Toren Vale'  vs  'the archivist on duty'
0.500  'Odo Bram'    vs  'the old ferryman'
```

These are deliberately the cases string matching cannot reach. A fixture full of
"Alice"/"Alice Vance" pairs would score beautifully and prove nothing.

---

## Running the golden novel through the real pipeline

```python
def ingest_fixture(db: DBClient, *, use_mock_llm: bool = True) -> str:
    """Create the golden novel and run every chapter through analyze_chapter."""
    key = load_answer_key()
    novel_id = str(db.fetchval("INSERT INTO novels (title) VALUES (%s) RETURNING id",
                               (key["novel"]["title"],), commit=True))
    try:
        for number, text in load_chapters():
            analyze_chapter(
                novel_id=novel_id, chapter_number=number, raw_text=text,
                use_mock_llm=use_mock_llm,
                # Pinned (not settings.chunk_size/overlap) so a developer's
                # CHUNK_SIZE env can't perturb chunking under the eval's
                # zero-margin recall floor.
                chunk_size=2000, chunk_overlap=200,
                db=db, replace=False, source="human",
            )
    except BaseException:
        db.execute("DELETE FROM novels WHERE id = %s", (novel_id,))
        raise
    return novel_id
```

Two details worth stealing.

**Chunk size is pinned, not read from config.** If it came from the environment, a
developer with `CHUNK_SIZE=500` in their shell would get different chunking, different
extraction, different recall — and a failing eval with no code change. Evals must be
deterministic in everything except the thing being measured.

**The cleanup catches `BaseException`, not `Exception`.** `BaseException` also catches
`KeyboardInterrupt` and `SystemExit`. Hit Ctrl-C mid-ingest and the half-built novel is
still deleted. Without it, the caller never receives the `novel_id`, so nobody can clean
it up — and the next run finds a duplicate.

The comment says the rest:

> *Each chapter commits its own transaction, so a mid-run failure would otherwise strand
> a partially-ingested novel (callers never see the novel_id to clean it up) — delete it
> before re-raising.*

---

## The four evals

### 1. Extraction fidelity — did we find the facts?

Ingest, read the projections back, score against the key.

```python
def _fidelity_report(db, novel_id) -> dict:
    key = load_answer_key()
    got = read_projections(db, novel_id)
    report = {kind: score_entities(key["entities"][kind], got["entities"][kind])
              for kind in ("characters", "locations", "objects", "factions")}
    report["possessions"] = score_possessions(key["possessions"], got["possessions"])
    report["knowledge"]   = score_knowledge(key["knowledge"], got["knowledge"])
    return report
```

The scoring functions are where the judgement lives. Consider name matching:

```python
"""Name matching is deliberately forgiving (casefold + containment either way):
extraction may emit "Mira" where the key says "Mira Solen", and that is a
correct extraction, not a miss."""

def _names_match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return a == b or a in b or b in a
```

If the extractor produces "Mira" and the key says "Mira Solen", that is *not* an error.
The extractor found the character. Penalising it would push you to tune for a naming
convention rather than for accuracy — you'd be measuring cosmetics.

But forgiveness has a cost, and the eval doesn't pretend otherwise. `"the thief"` and
`"the thief who stole the lantern"` also match under containment. Every scoring function
is a compromise between "too strict, penalises correct behaviour" and "too loose,
rewards wrong behaviour." Writing the compromise down in the docstring is the minimum
honest thing to do.

Possession intervals get a tolerance:

```python
def score_possessions(expected, actual, tolerance: int = 1):
    """Recall of expected possession intervals among the actual edges.

    An expected interval counts as found when some actual edge has the same
    object+holder (fuzzy names) and boundaries within `tolerance` chapters.
    """
```

Why ±1 chapter? Because "when did Toren stop having the lantern" is genuinely
ambiguous. Chapter 5 is when Kessa steals it — but she steals it *at night*, and the
chapter opens with Toren still holding it. Both chapter 4 and chapter 5 are defensible
answers. A zero-tolerance metric would flag a correct extraction as wrong, and you'd
"fix" it by making the extractor worse.

Knowledge matching uses word overlap with a floor:

```python
def _facts_overlap(expected_fact: str, actual_fact: str) -> bool:
    exp_words = set(_norm(expected_fact).split())
    act_words = set(_norm(actual_fact).split())
    return len(exp_words & act_words) >= max(2, len(exp_words) // 2)
```

At least half the expected fact's words, with a floor of 2. And the docstring names the
consequence:

> *Expected facts must therefore be at least ~4 words long to be matchable at 50%
> overlap; the golden answer key's facts all are.*

That's a constraint the *metric* places on the *fixture*, written down where the fixture
author will read it. It's the kind of coupling that silently rots otherwise — someone
adds a two-word knowledge fact, it can never match, and the eval reports a permanent
recall miss with no explanation.

### 2. Retrieval recall@k — do we find the right chapters?

Ten queries, each with known correct chapters. Run them through the *production* search
path and measure how many expected chapters appear in the top k.

```python
def recall_at_k(expected_chapters, result_chapters, k) -> float:
    """Fraction of expected chapters that appear in the top-k results."""
    if not expected_chapters:
        return 1.0
    top = set(result_chapters[:k])
    return sum(1 for c in expected_chapters if c in top) / len(expected_chapters)
```

Why **recall** and not precision? Because of what the results are *for*. Retrieval feeds
context to a downstream consumer — a model, or a human reading a search page. Returning
the right chapter plus three irrelevant ones is a mild waste. *Missing* the right chapter
is a hard failure: the consumer never sees it and can't recover. Asymmetric costs
justify an asymmetric metric.

Why **k = 8**? It's the default `k` for the search endpoint. Measure what ships.

And the eval goes through `reads.search.search()` — the same function the wiki route and
the MCP tool call — not through a test harness that reimplements retrieval. An eval that
tests a parallel implementation measures the parallel implementation.

### 3. Critic precision/recall

Covered in Post 8: four seeded violations, one clean draft, `recall == 1.0` and
`false_positives == 0` asserted exactly. Deterministic, because there's no LLM in this
loop — the claims are constructed directly.

And the known flaw, from the project's own gap list: precision cannot fall below 0.8 by
construction, because there is only one negative case. A "flag everything" critic scores
0.8/1.0. The metric can't distinguish correct from paranoid.

### 4. Entity resolution — pairwise

Post 4 covered the mechanism. Two things bear repeating because they're the transferable
part.

**The two error kinds are never traded off:**

```python
assert not report["false_merges"], report["false_merges"]     # ZERO tolerated
assert report["recall"] >= 0.6, report["missed_merges"]       # 40% misses tolerated
```

A single wrong merge fails the eval. Missing 40% of merges passes. That asymmetry is the
cost model from Post 4 encoded as an executable rule, and it means the eval will never
approve a change that trades a wrong merge for two right ones.

**Unextracted surfaces are excluded and reported separately:**

```python
"coverage": len(gradeable) / len(truth) if truth else 1.0,
"not_extracted": sorted(set(truth) - set(predicted)),
```

If extraction never emitted "the old ferryman", that's an extraction failure, not a
resolution failure. Folding them together makes the resolution metric move for reasons
that have nothing to do with resolution — and then you'd "improve resolution" by
improving extraction, learn nothing, and possibly regress the thing you meant to fix.

> **A metric that moves for the wrong reason is worse than no metric, because you will
> act on it.**

### And the one with no ground truth at all

The fifth measurement, and the most practically useful:

```python
def count_duplicate_pairs(db, novel_id) -> dict:
    """Duplicate-looking entity pairs — see `pipeline.db.duplicates`.

    Re-exported here so the eval and the `/entities/duplicates` route grade the
    same thing. If these two ever diverge, the eval stops measuring what the
    product actually surfaces.
    """
```

It scores every entity pair with the canonicalizer's own similarity function at its own
threshold, and splits the count same-type versus cross-type. No answer key needed — so it
runs against **any** ingested novel, not just the fixture.

That's what found the 14-cross-type-vs-1-same-type result from Post 4. The
ground-truth-based eval could only ever run on a ten-chapter hand-written fixture,
which — being hand-written by someone who understood the type system — contains no
cross-type confusions at all. The fixture was *too clean to contain the bug*.

> **Ground-truth evals measure what you thought to label. Ground-truth-free diagnostics
> measure what's actually there.** You want both, and the second one is where the
> surprises live.

And note the re-export. The eval calls the *same function* the `/entities/duplicates`
endpoint serves, so the number in the eval and the list shown to users cannot drift.
Same principle as the read layer in Post 9, applied to a metric.

---

## Mock vs real: two modes, honestly labelled

Real-LLM evals cost money and need network access. Mock evals are free and offline. The
harness runs both, and is careful about which is which.

```bash
cd backend && .venv/bin/python -m pytest evals/           # offline, mock LLM
cd backend && RUN_LLM_EVALS=1 .venv/bin/python -m pytest evals/   # + real-LLM evals
```

```python
@pytest.mark.skipif(
    os.getenv("RUN_LLM_EVALS") != "1",
    reason="real-LLM extraction fidelity: set RUN_LLM_EVALS=1 (spends API credits)",
)
def test_extraction_fidelity_real_llm(db):
    ...
```

The mock mode's job is explicitly *not* quality:

```python
def test_fidelity_harness_runs_in_mock_mode(db, golden_novel):
    """Plumbing test: the full ingest -> projections -> scoring path works.

    Mock extraction is not expected to match the answer key, so no
    thresholds here — only that every metric computes and is well-formed.
    """
    for kind in ("characters", "locations", "objects", "factions"):
        assert 0.0 <= report[kind]["recall"] <= 1.0
```

The only assertions are that the numbers are in range. It tests that ingest → projections
→ scoring works end to end. It says nothing about accuracy, and doesn't pretend to.

That's a real category of test worth naming: **a plumbing eval**. It catches "the scoring
function crashes on empty input" and "the projection query returns the wrong column
name", which are the failures that would silently disable your real eval.

### The honesty fix

Post 7 covered this; it belongs here too because it's fundamentally about measurement
integrity.

The offline retrieval eval uses hash embeddings. Those carry no semantic signal — two
strings differing by a trailing period measure ≈ −0.016 cosine, two unrelated texts ≈
−0.001. And they're fused into RRF as three of six ranked lists at equal weight.

So the offline number is not hybrid retrieval quality. It's BM25 recall degraded by
noise. The docstring says so, at length:

```python
"""``use_real_embeddings=False`` (default) is the offline regression gate. The
fixture is ingested with mock embeddings, so both sides are hash vectors.
Those carry **no** semantic signal ... So the dense channel is not merely
uninformative here, it is active noise, and the resulting number is BM25
recall degraded by a random channel. It is a legitimate *regression* signal
(it moves when the plumbing breaks) and a meaningless *quality* one, so it
is reported as ``bm25_recall_at_k`` and must never be quoted as hybrid
retrieval quality."""
```

And the metric name changes with the mode:

```python
metric = "hybrid_recall_at_k" if use_real_embeddings else "bm25_recall_at_k"
return {"metric": metric, metric: mean,
        "dense_channel": "real" if use_real_embeddings else "hash (noise)", ...}
```

With a test whose only job is to stop the misleading label coming back:

```python
def test_offline_metric_is_labelled_bm25_not_hybrid(db, golden_novel):
    """The offline number must not present itself as hybrid retrieval quality.
    ... Reporting it as hybrid recall overstates what was measured; the label
    is the guard against that number being quoted."""
    assert report["metric"] == "bm25_recall_at_k"
    assert "hybrid_recall_at_k" not in report
    assert report["dense_channel"] == "hash (noise)"
```

A regression test for intellectual honesty. The failure mode it prevents isn't a crash —
it's someone putting "retrieval recall: 1.0" on a slide.

---

## The thresholds, and where each came from

| Metric | Bar | Mode |
|---|---|---|
| Critic recall on seeded violations | **= 1.0** exact | offline |
| Critic false positives on a clean draft | **= 0** exact | offline |
| Retrieval mean recall@8 | **≥ 0.9** | offline (BM25) |
| Hybrid recall@8 with real embeddings | **≥ 0.9** | `RUN_LLM_EVALS=1` |
| Extraction: characters recall | **≥ 0.75** | `RUN_LLM_EVALS=1` |
| Extraction: locations recall | **≥ 0.75** | `RUN_LLM_EVALS=1` |
| Extraction: objects recall | **≥ 1.0** | `RUN_LLM_EVALS=1` |
| Extraction: possessions recall | **≥ 0.6** | `RUN_LLM_EVALS=1` |
| Extraction: knowledge recall | **≥ 0.5** | `RUN_LLM_EVALS=1` |
| ER: false merges | **= 0** exact | `RUN_LLM_EVALS=1` |
| ER: pairwise recall | **≥ 0.6** | `RUN_LLM_EVALS=1` |
| ER: type accuracy | **≥ 0.8** | `RUN_LLM_EVALS=1` |
| Cross-type duplicate pairs (fixture) | **= 0** exact | `RUN_LLM_EVALS=1` |

The spread is the interesting part. Objects must be perfect; knowledge only has to clear
50%. That's not laziness, it's a difficulty ranking:

**Objects: 1.0.** There is one object in the fixture, named explicitly, capitalised,
central to every chapter. If the extractor misses the Brass Lantern, something is
catastrophically wrong.

**Characters and locations: 0.75.** Four and four, all named. But "the archivist on duty"
and "the reading-room" are genuinely hard. 75% means three of four.

**Possessions: 0.6.** Requires *both* correct extraction of a transfer *and* correct
replay into an interval *and* correct interval boundaries within ±1. Three things in
series, each imperfect.

**Knowledge: 0.5.** The hardest category by the widest margin — the benchmarks from Post 1
put the best models at 69% against a 92% human baseline on this exact task. Demanding 90%
would be demanding the impossible, and the eval would just get disabled.

> **A threshold you can't hit gets deleted. A threshold set below current behaviour
> catches nothing. The useful range is "just above what a working system does", and
> finding it requires knowing what a working system does.**

Which brings us to the trap.

---

## The threshold that certified a broken system

Retrieval recall@8 had a floor of **0.5**.

Where did that come from? From running the system and picking a number just under the
observed value. Which is the natural thing to do, and it's exactly wrong.

Post 7 catalogued three bugs — MMR discarding the fused hybrid score, two incompatible
score scales, BM25 ANDing every term so natural-language queries matched nothing. All
three were live when the floor was set.

The broken system scored **exactly 0.5**. It passed. Every day. For weeks.

The floor didn't encode "retrieval should work." It encoded "retrieval should be no worse
than the day I wrote this test" — and that day, it was broken.

After the fixes:

```python
# Raised 0.5 -> 0.9 on 2026-08-14. The old floor was set while BM25 used
# AND semantics and MMR reranked on query-embedding cosine, which scored
# exactly 0.5; leaving it there would let that regression back in silently.
# Full-recall queries score 1.0, so 0.9 tolerates one query slipping.
assert report["mean_recall_at_k"] >= 0.9, report["per_query"]
```

It now scores **1.00**.

The generalisable rule:

> **Derive thresholds from what the system *should* do, then investigate the gap.** If
> the fixture has ten queries and each one's answer is a specific chapter, correct
> behaviour is recall 1.0. A system scoring 0.5 is failing half its queries — that's a
> bug report, not a baseline.

The failure mode has a name in other fields — anchoring — and it's endemic in ML
evaluation, where "measure, then set the bar just below" is standard practice. It works
fine as a *regression* detector. It is useless as a *quality* bar, and the two get
conflated constantly.

A weaker but still useful version of the discipline: when you set a threshold from
observation, **write down why the observed value is acceptable.** If you can't, you've
just learned something.

---

## Making failures readable

Small thing, big payoff. When an eval fails you need to know *which case*, not just that
the average dropped:

```python
def _print_resolution(report: dict) -> None:
    print(f"\nER pairwise: p={report['precision']:.2f} r={report['recall']:.2f} "
          f"f1={report['f1']:.2f} coverage={report['coverage']:.2f} "
          f"type_acc={report['type_accuracy']:.2f}")
    for a, b in report["false_merges"]:
        print(f"  FALSE MERGE  {a!r} + {b!r}")
    for a, b in report["missed_merges"]:
        print(f"  missed merge {a!r} + {b!r}")
    for row in report["mistyped"]:
        print(f"  mistyped     {row['surface']!r} {row['expected']} -> {row['got']}")
    for surface in report["not_extracted"]:
        print(f"  not extracted {surface!r}")
```

Run with `pytest -s` and you get:

```
ER pairwise: p=1.00 r=0.71 f1=0.83 coverage=0.85 type_acc=0.92
  missed merge 'the thief' + 'kessa dray'
  missed merge 'the old ferryman' + 'odo bram'
  mistyped     'vault-key' object -> character
  not extracted 'harbor-girl'
```

Four lines that tell you: descriptive references are being missed (the known hard case),
one object got typed as a character (cross-type, the known open gap), and one surface
never got extracted at all (an extraction miss, not a resolution one).

And the assertions carry the detail into the failure message:

```python
assert report["recall"] >= 0.6, report["missed_merges"]
```

The second argument to `assert` is printed when it fails. So a failing eval doesn't say
`AssertionError: 0.55 >= 0.6`; it prints the actual pairs that were missed. **The distance
between "a test failed" and "I know what to fix" should be zero.**

---

## What the evals don't measure

Being precise about coverage is as important as the coverage itself.

**Cost.** Nothing measures tokens, dollars, or latency per chapter. A change that doubles
extraction cost and improves recall by 2% would sail through. This is a named open gap:

> *No per-chapter cost/token accounting, no model tiering. A chapter is 13 extraction
> passes plus dedup, canonicalization, multi-summaries, scenes, knowledge, and
> commitments, all on one model.*
>
> *Done when: a per-chapter run record exists (tokens, cost, timings, finding counts) and
> low-stakes passes can route to a cheaper model.*

**Scale.** The fixture is 10 chapters and 4,000 words. Nothing measures what happens at
70 chapters with 200 entities — where the canonicalizer's roster cap starts biting, where
the materializer's full-novel rebuild gets slow, where prompt-context selection starts
dropping people who matter.

**Generalisation.** One fixture, one genre, one author's prose. The LitRPG cross-type
finding came from pointing a diagnostic at a *different* kind of book, and it found
something the fixture structurally could not contain. One fixture is one sample.

**Anything downstream.** Continuum's job is to be correct memory. Whether an agent using
it produces a *better novel* is unmeasured, and honestly hard to measure — you'd need
human evaluation of long-form fiction, which is its own research problem.

**The critic's precision, properly.** Already covered: the metric's floor is 0.8 by
construction.

Every one of those is written down in the project's own gap list, with an exit condition.
That's the standard worth holding: **an unmeasured property is fine, as long as you know
it's unmeasured.** The dangerous state is thinking you've measured something you haven't.

---

## What the evals bought

Concretely, in this project:

- The **cross-type duplicate discovery** — 14 vs 1 — which redirected months of
  misallocated effort. (Post 4.)
- The **retrieval ranking bugs**, exposed when the floor was raised from a number the
  broken system happened to hit. (Post 7.)
- A **falsifiable claim**. "The redesign is complete" stopped being an assertion and
  became a thing that runs in CI.

And one meta-benefit that's easy to undervalue: having an eval changes how you argue.
"Should we merge passes to cut cost?" becomes a question with an answer — try it, run the
eval, look at the delta — rather than a debate between two people's intuitions. The eval
harness's real product isn't the numbers. It's that the numbers exist, so disagreements
terminate.

---

## Where we are

Ten posts. A system that extracts, resolves, tracks, models, searches, checks, serves, and
measures.

There's one part left, and it's the part that doesn't appear in any architecture diagram:
the work of making it not fall over. Transactions that actually roll back. A connection
pool sized for the server that uses it. Indexes on the columns the queries touch. A lock
around a rebuild that two workers can run at once.

That work found a bug where `analyze_chapter` returned a success payload containing a
chapter id for a row that did not exist — with a fully green test suite, because every
handler involved was marked `# pragma: no cover`.

---

*Next: [Part 11 — The Unglamorous Twenty Percent](11-production.md) — transactions,
savepoints, pool sizing, indexes, a silent data-loss bug, why deleting six weeks of
working code was the right call, and the six things this system still gets wrong.*
