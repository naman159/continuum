# Part 7 — Finding Things

*Part 7 of the Continuum series. [Part 5](05-time.md) gave us exact answers to exact
questions — who holds the dagger at chapter 31 — and [Part 6](06-modelling-a-world.md)
finished the world model. This post is about the questions that have no exact answer, and
about how a carefully-built "hybrid" search engine can be silently not hybrid at all.*

---

## The questions tables can't answer

Everything so far has been lookups. `possesses_edges` has a row; you filter it; you get
an answer. Beautiful when it applies.

Now try these:

> *"Find the scene where Mira first doubted Toren."*
>
> *"Has anyone mentioned the sunken vault before?"*
>
> *"What happened at the harbour?"*

There is no `doubts` table. There never will be — you'd need a schema entry for every
emotion, and then for every shade of every emotion. "First doubted" isn't a fact you can
enumerate in advance; it's a *fuzzy semantic query over prose*.

So we need search. And "search" turns out to mean two completely different things that
fail in opposite directions.

For the rest of this post I'll use a small worked example. The golden test novel in this
project, *The Lantern of Veyra*, is ten short chapters about a brass lantern that keeps
changing hands. Chapter 1 begins:

> *Mira Solen had worked the tide-tables at the Harbor of Veyra since she was twelve,
> and she knew every crate that came off the ferries. The one that split open on the
> quay that morning was not on any manifest. Inside, wrapped in oilcloth, lay a brass
> lantern, cold to the touch and heavier than it had any right to be.*

Ten chapters, ten test queries with known correct answers. We'll use it throughout.

---

## Search, attempt 1: keywords

The oldest idea in information retrieval, and still the best baseline: find documents
containing the words in the query.

### How a keyword index actually works

Suppose you have 70 chapters and someone searches for "lantern". The naive approach
scans every chapter looking for the substring — that's `grep`, and it's O(total text) on
every query.

The trick that makes real search engines fast is the **inverted index**. Instead of
mapping documents to their words, you map words to their documents:

```
"lantern"  →  {ch1, ch2, ch3, ch4, ch5, ch7, ch8, ch9}
"harbor"   →  {ch1, ch7}
"vault"    →  {ch3, ch9}
"ferryman" →  {ch7}
```

Now a query is a dictionary lookup, not a scan. This is *the* foundational data structure
of search; everything else is refinement.

Two refinements matter for us.

**Stemming.** You want a search for "opens" to match "opened" and "opening". So words are
reduced to a root form before indexing: `opens → open`, `carried → carri`, `lanterns →
lantern`. It's crude — it's a rule-based chopper, not a dictionary — but it works well
enough that every search engine does it.

**Stop words.** Words like "the", "of", "and" appear in every document, so they carry no
discriminating information. They're dropped. This is why `to_tsvector` on our chapter 1
sentence produces something like:

```
'brass':17 'cold':21 'crate':13 'ferri':16 'harbor':6 'lantern':18
'mira':1 'morn':30 'quay':28 'solen':2 'tide':4 'tabl':5 ...
```

Note: stemmed (`morning → morn`, `table → tabl`, `ferries → ferri`), stop words gone,
and each term tagged with its position in the document.

### Ranking: TF-IDF and BM25

Finding the matching documents is the easy half. Ordering them is the interesting half.

The core intuition has two parts.

**Term frequency (TF)**: a document that says "lantern" nine times is probably more about
lanterns than one that says it once.

**Inverse document frequency (IDF)**: a term that appears in *every* document tells you
nothing. If all ten chapters mention "lantern", matching on "lantern" doesn't
distinguish them. But "ferryman" appears in exactly one — matching on it is enormously
informative. So each term is weighted by how rare it is:

```
idf(term) ≈ log( total_documents / documents_containing_term )
```

Multiply them and you get **TF-IDF**, the classic scoring function.

**BM25** ("Best Match 25", from a 1990s research programme) is TF-IDF with two fixes
learned from experience:

1. **Term-frequency saturation.** Going from one mention to five is a big signal. From
   fifty to fifty-five is not. Raw TF is linear; BM25 applies a saturating curve, so
   extra repetitions have diminishing returns.

   ```
   score
     ▲
     │        ....................  BM25 (saturates)
     │      ..
     │    ..              /
     │  ..            /        raw TF (linear — a keyword-stuffed
     │ .          /            document wins forever)
     │.      /
     └────────────────────────▶ term frequency
   ```

2. **Length normalisation.** A 10,000-word chapter will contain more of everything than a
   500-word chapter, purely by being longer. BM25 divides out document length so long
   documents don't automatically win.

Postgres ships all of this natively, which is one of the reasons Post 2 chose Postgres.
The types are `tsvector` (a document's indexed terms) and `tsquery` (a parsed query),
and the ranking function is `ts_rank_cd`. It's not literally the BM25 formula — it's
Postgres's own cover-density ranking — but it's the same family and the codebase calls
it "BM25-like", which is honest.

### Setting it up

Each searchable table gets a `tsvector` column, kept current by a trigger:

```sql
ALTER TABLE chapters ADD COLUMN search_tsv tsvector;

CREATE FUNCTION chapters_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.search_tsv :=
    setweight(to_tsvector('english', coalesce(NEW.title, '')),          'A') ||
    setweight(to_tsvector('english', coalesce(NEW.summary_short, '')),  'A') ||
    setweight(to_tsvector('english', coalesce(NEW.summary, '')),        'B') ||
    setweight(to_tsvector('english', coalesce(NEW.summary_long, '')),   'C') ||
    setweight(to_tsvector('english', coalesce(NEW.raw_text, '')),       'D');
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER chapters_tsv_trigger BEFORE INSERT OR UPDATE ON chapters
FOR EACH ROW EXECUTE FUNCTION chapters_tsv_update();
```

Three things here.

**A trigger, not application code.** The `search_tsv` column updates itself on every
insert or update, in the database. There is no code path that can write a chapter and
forget to index it. Whenever a derived value must *never* drift from its source, pushing
the derivation into the database is the strongest available guarantee.

**Weighted fields.** Postgres supports four weight classes, A through D, with default
multipliers of 1.0, 0.4, 0.2 and 0.1. A term matching in the title (A) counts ten times
as much as the same term buried in the raw prose (D). That encodes an editorial
judgement: *if a word made it into the one-line summary, this chapter is about that
word.* If it appears once in 4,000 words of prose, it's incidental.

**GIN indexes.**

```sql
CREATE INDEX idx_chapters_tsv ON chapters USING GIN (search_tsv);
```

GIN ("Generalized Inverted Index") is Postgres's inverted-index implementation. It's the
data structure from the top of this section, exposed as an index type. Without it, every
search sequentially scans the table.

### Then it returned nothing

Here's the first bug, and it's a good one because the broken code looks completely
reasonable.

Postgres has several functions for turning user input into a `tsquery`. The obvious one
is `plainto_tsquery`, which takes arbitrary text and produces a query. Here's what it
does:

```sql
SELECT plainto_tsquery('english', 'Jake fights the beast in the tutorial');
      ──▶  'jake' & 'fight' & 'beast' & 'tutorial'
```

Note the `&`. **It ANDs every term.** A document must contain *all four* stems to match.

Which is fine for a search box where people type two keywords. It is a disaster for
natural-language questions, which is exactly what an AI agent asks. On a real ingested
novel, that query returned **0 matches out of 27 events**. Not badly ranked — *zero*.
Every event was about Jake, several were about the beast, one was literally the tutorial
fight. None contained all four stems in one row.

And here's the insidious part: from the outside, that's indistinguishable from "nothing
relevant exists." The API returns 200 OK with an empty list. There's no error to
investigate.

The fix relaxes AND to OR, while keeping input sanitisation:

```sql
-- websearch_to_tsquery sanitizes arbitrary user input, but it ANDs every term,
-- so a document missing a single word of the query drops out entirely -- which
-- zeroes out recall for natural-language questions. Relaxing '&' to '|' keeps
-- partial matches in the candidate pool; ts_rank_cd then ranks documents that
-- match more of the query above those that match less. Negated terms
-- ("-foo" -> "!foo") keep AND semantics, since OR-ing a negation would match
-- nearly every row.
WITH tq AS (
    SELECT CASE
             WHEN strpos(t::text, '!') > 0 THEN t
             ELSE replace(t::text, '&', '|')::tsquery
           END AS query
    FROM websearch_to_tsquery('english', %(q)s) AS t
)
```

Three decisions packed into eight lines:

**Why `websearch_to_tsquery` and not `to_tsquery`?** `to_tsquery` requires
already-valid query syntax and *throws an exception* on arbitrary text — a user typing
an apostrophe crashes the endpoint. `websearch_to_tsquery` accepts anything a person
might type into Google, including quoted phrases and `-excluded` terms, and never
raises.

**Why rewrite the query as text?** Because `websearch_to_tsquery` returns a parsed
`tsquery` with AND baked in and there's no flag to change it. Casting to text, replacing
`&` with `|`, and casting back is a hack — and it's the pragmatic one, because the
alternative is reimplementing query parsing.

**Why the negation carve-out?** In `tsquery`, `!foo` means "must not contain foo". OR-ing
a negation gives you `a | !b`, which matches every document not containing `b` — i.e.
nearly everything. Queries with negation keep AND semantics.

After the fix, that same query matched **22 of 27** events. From 0% recall to 81% by
changing one operator.

And crucially, ranking still works. OR semantics widen the *candidate pool*;
`ts_rank_cd` then sorts documents matching more of the query above those matching less.
The document with all four terms still wins — it's just no longer the case that anything
short of all four is invisible.

> **Generalisable lesson: recall and precision live in different stages.** Let retrieval
> be generous and let ranking be strict. A candidate that was never retrieved cannot be
> ranked back into existence.

### Where keyword search still fails

It's fast, cheap, exact, and explainable. But:

```
query: "Where does Mira feel uneasy about the archivist?"
```

The chapter says *"Toren Vale locked the reading-room door before he spoke."* The word
"uneasy" doesn't appear. Neither does "archivist" — his title appears once, three
chapters earlier. Zero overlap. Zero match.

Keyword search matches *strings*. It has no idea that "uneasy" and "locked the door
before he spoke" are related, or that "archivist" and "Toren Vale" are the same person.

For that we need something that understands meaning.

---

## Search, attempt 2: embeddings

### What a vector is (starting from nothing)

A **vector** is a list of numbers. That's all.

```python
[0.021, -0.443, 0.117, ..., 0.008]     # 1536 numbers
```

You can think of a list of *three* numbers as a point in 3D space — x, y, z. A list of
1536 numbers is a point in 1536-dimensional space. You can't picture that, and you don't
need to; all the operations we care about work identically in any number of dimensions.

### What an embedding is

An **embedding model** is a neural network that takes text and outputs a vector, trained
so that **texts with similar meanings get vectors that are close together in space.**

That's the whole idea. The training doesn't need to be understood to use it; what
matters is the property it produces:

```
"the cat sat on the mat"        →  [0.21, -0.05, 0.88, ...]
"a feline rested on the rug"    →  [0.19, -0.07, 0.85, ...]   ← close!
"quarterly revenue projections" →  [-0.62, 0.44, -0.11, ...]  ← far
```

The first two share no content words. Their vectors are nearly identical. That's what
keyword search can't do, and it's why embeddings exist.

Continuum uses `text-embedding-3-small` by default, producing 1536-dimensional vectors.

### Measuring "close"

The standard measure is **cosine similarity**: the cosine of the angle between two
vectors.

```
        cos(θ) = (A · B) / (‖A‖ ‖B‖)

           B
          ↗
         ╱ θ
        ╱────────▶ A

   θ = 0°    →  cos = 1.0    identical direction  (same meaning)
   θ = 90°   →  cos = 0.0    perpendicular        (unrelated)
   θ = 180°  →  cos = −1.0   opposite direction   (opposite meaning)
```

`A · B` is the **dot product**: multiply the vectors element-wise and sum. `‖A‖` is the
vector's length (square root of the sum of squares). Dividing by the lengths is what
makes it measure *direction only* — which is what you want, since a longer piece of text
shouldn't be "more" of anything.

Here's the actual implementation used for the diversification step later in this post,
which is as plain as it looks:

```python
def _cosine(a: list[float], b: list[float]) -> float:
    dot = na = nb = 0.0
    n = min(len(a), len(b))
    for i in range(n):
        av, bv = a[i], b[i]
        dot += av * bv
        na  += av * av
        nb  += bv * bv
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
```

Postgres, with the `pgvector` extension, gives you a `VECTOR(1536)` column type and the
`<=>` operator for **cosine distance** (which is just `1 − cosine similarity`, so smaller
is better):

```sql
SELECT id, summary, (embedding <=> %(qvec)s::vector) AS distance
FROM chapters
WHERE novel_id = %(novel_id)s
ORDER BY embedding <=> %(qvec)s::vector
LIMIT 50
```

And the conversion back, with a defensive clamp:

```python
distance = float(row["distance"])
similarity = 1.0 - distance
# Cosine distance can be in [0, 2] for non-normalized vectors;
# clamp similarity to [0, 1] for downstream blending.
similarity = max(0.0, min(1.0, similarity))
```

### Making it fast: approximate nearest neighbours

Finding the closest vector to a query means comparing against every stored vector. With
1536 dimensions and 5,000 rows that's 7.7 million multiply-adds per query. Doable, but
it scales linearly and you're doing it on every search.

The fix is **ANN — approximate nearest neighbour** search: give up the guarantee of
finding the *exact* closest vectors in exchange for finding *almost always the right
ones*, far faster. For search results, "almost always" is fine — the ranking is fuzzy
anyway.

Postgres/pgvector offers two index types, and the project switched from one to the other.

**IVFFlat** ("inverted file with flat compression") partitions the vectors into clusters
using k-means, and stores the centroid of each. At query time you find the nearest few
centroids and search only inside those clusters.

```
        ·  ·                  ╭─ cluster 1 ─╮
      ·  ·  ·                 │  · · ·      │
                              │   ·(c1)·    │
    ·  ·     ·      ──▶       ╰─────────────╯
       ·   ·                  ╭─ cluster 2 ─╮
         ·                    │   ·(c2)·    │
                              ╰─────────────╯
    query → nearest centroid → search that cluster only
```

Fast, small, and it has a specific failure: **a true nearest neighbour sitting just
across a cluster boundary is missed entirely.** You searched the wrong bucket. There's no
recovery.

**HNSW** ("Hierarchical Navigable Small World") builds a multi-layer graph. Each vector
is a node connected to its neighbours. The top layer is sparse with long-range links;
each layer down is denser. Searching means entering at the top, greedily walking toward
the query, then dropping a layer and refining.

```
  layer 2   ●─────────────────────●          few nodes, long hops
             ╲                   ╱
  layer 1   ●───●───────●───────●            more nodes
             ╲  │      ╱ ╲     ╱
  layer 0   ●─●─●─●─●─●───●─●─●─●            every node, short hops
            └──────── refine here ───────┘
```

It's the same idea as a skip list, or as travelling by taking a plane, then a train, then
a taxi. Better recall than IVFFlat at comparable speed; costs more memory and is slower
to build.

The schema records the switch as a decision:

```sql
-- ---- Switch IVFFlat -> HNSW (better recall at novel scale) ----
DROP INDEX IF EXISTS idx_chapters_embedding;
DROP INDEX IF EXISTS idx_events_embedding;
CREATE INDEX IF NOT EXISTS idx_chapters_embedding_hnsw
    ON chapters USING hnsw (embedding vector_cosine_ops);
```

At novel scale — thousands of vectors, not billions — HNSW's higher memory cost is
irrelevant and its better recall is free. Different call at a different scale.

### Where embeddings fail

Now the counter-example, and it's the exact mirror of keyword search's failure.

```
query: "Aelric's silver dagger"
```

The novel contains three daggers: Aelric's silver one, a bronze one in the armoury, and
"the dagger" that Kessa carries. All three passages embed to nearly the same place,
because *semantically they are nearly the same thing* — a person, a small blade, a
threat.

The embedding has smeared exactly the distinction you asked about. The word "silver" is
one adjective in a sentence; the model has compressed the sentence's meaning into 1536
numbers, and "silver vs bronze" contributes almost nothing to that compression compared
to "this is about a dagger".

The same failure hits proper nouns hardest, which for a novel is catastrophic. "Mira" and
"Kessa" are both short names of women in the same book, appearing in similar contexts.
Their embeddings are *close*. A dense search for "Mira at the harbour" will happily
return the Kessa passage.

This is not a fixable weakness; it's what embeddings *are*. They compress meaning, and
compression discards exactly the low-frequency, high-information tokens — names, item
identifiers, rule names — that continuity errors depend on.

```
┌───────────────────────────────────────────────────────────────────────┐
│                                                                       │
│  KEYWORD (BM25)                    DENSE (embeddings)                 │
│  ───────────────                   ──────────────────                 │
│  ✓ exact names                     ✗ smears names together            │
│  ✓ rare terms are strong           ✗ rare terms wash out              │
│  ✓ cheap, no model call            ✗ costs an embedding call          │
│  ✓ explainable                     ✗ opaque                           │
│  ✗ no synonyms                     ✓ synonyms, paraphrase             │
│  ✗ no paraphrase                   ✓ "uneasy" ↔ "locked the door"     │
│  ✗ zero results on vocab mismatch  ✓ always returns something         │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

Read that table again. Every ✓ on the left is a ✗ on the right and vice versa. That's not
a coincidence — it's why the answer is to run both.

---

## Search, attempt 3: hybrid, and the fusion problem

Running both is trivial. Combining the results is not, and the reason is worth
understanding because it's a genuinely counter-intuitive result.

### Why you can't just add the scores

BM25 returns `ts_rank_cd` scores. Dense search returns cosine similarities. Here's a real
shape of the problem:

```
BM25 scores:    0.089, 0.071, 0.064, 0.019, 0.004     ← unbounded, tiny, clustered low
Dense scores:   0.83,  0.81,  0.79,  0.78,  0.77      ← bounded [0,1], clustered high
```

Add them and dense wins every time, not because it's more relevant but because its
numbers happen to be bigger.

Normalise each to [0,1] first? Better, but still broken, because the *distributions* are
different shapes. Dense scores cluster tightly — everything semantically related to a
query scores 0.75–0.85, so after normalisation the gap between the best and tenth-best
result is tiny. BM25 scores spread widely, so normalisation preserves large gaps. Adding
normalised scores lets BM25's spread dominate the ranking even when dense is more
confident. You've swapped one arbitrary weighting for another.

And whatever weights you tune, they're tuned for *this* corpus and *these* query shapes.
Change the model and they're wrong again.

### Reciprocal Rank Fusion

The elegant answer: **throw away the scores entirely and use only the ranks.**

```
RRF(document d) = Σ over lists   1 / (k + rank of d in that list)
```

with `k = 60` by convention (a value from the original 2009 paper; the result is not
sensitive to it).

```python
def reciprocal_rank_fusion(*ranked_lists, k: int = 60, limit: int = 50):
    fused_scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_idx, result in enumerate(ranked, start=1):
            item_id = result.item_id
            fused_scores[item_id] = fused_scores.get(item_id, 0.0) + 1.0 / (k + rank_idx)
            ...
```

Worked example. Two lists:

```
BM25:   1. ch7    2. ch1    3. ch9
Dense:  1. ch1    2. ch3    3. ch7

ch1:  1/(60+2)  +  1/(60+1)   =  0.01613 + 0.01639  =  0.03252   ← 1st
ch7:  1/(60+1)  +  1/(60+3)   =  0.01639 + 0.01587  =  0.03226   ← 2nd
ch9:  1/(60+3)                =  0.01587             =  0.01587   ← 3rd
ch3:              1/(60+2)    =  0.01613             =  0.01613
```

`ch1` wins: it was near the top of both lists. `ch7` is a very close second for the same
reason. Items appearing in only one list score roughly half as much.

Why this works so well:

**It's scale-free.** Ranks are ranks. You can fuse BM25 with dense with a
graph-traversal ranker with a human-curated list, and no calibration is needed.

**The `+k` damps the top.** Without it, rank 1 scores 1.0 and rank 2 scores 0.5 — a 2×
gap that lets any single list dictate the outcome. With `k=60`, rank 1 scores 0.0164 and
rank 2 scores 0.0161, a 2% gap. So *agreement between lists* matters more than *any one
list's confidence*. That's precisely the property you want when you're fusing signals
that fail in different directions.

**It degrades gracefully.** If dense search returns garbage, its contributions are spread
across items that BM25 ranked poorly, and the BM25 ordering survives.

Continuum fuses six lists — BM25 and dense, for each of chapters, scenes, and events:

```python
ranked_lists = []
for kind in active_kinds:                       # chapter, scene, event
    ranked_lists.append(per_kind.get(kind, {}).get("bm25", []))
    ranked_lists.append(per_kind.get(kind, {}).get("dense", []))
fused = reciprocal_rank_fusion(*ranked_lists, k=60, limit=_FUSION_POOL)
```

### The full pipeline

```
                    ┌────────────────────────────┐
                    │  RetrievalQuery            │
                    │  text + novel + max_chapter│
                    └─────────────┬──────────────┘
                                  │  embed once, reuse
              ┌───────────────────┼───────────────────┐
              ▼                                       ▼
   ┌──────────────────────┐              ┌──────────────────────┐
   │ BM25 × {chapter,     │              │ Dense × {chapter,    │
   │        scene, event} │              │        scene, event} │
   │ 50 candidates each   │              │ 50 candidates each   │
   └──────────┬───────────┘              └───────────┬──────────┘
              └───────────────┬───────────────────────┘
                              ▼
                  ┌───────────────────────┐
                  │  RRF  (k=60)          │  6 lists → 50 fused
                  └───────────┬───────────┘
                              ▼
                  ┌───────────────────────┐
                  │  LLM rerank (optional)│  cross-encoder scoring
                  └───────────┬───────────┘
                              ▼
                  ┌───────────────────────┐
                  │  MMR (λ = 0.7)        │  diversify → top k
                  └───────────┬───────────┘
                              ▼
                        final results
```

The two stages after fusion are worth understanding.

### Reranking: the cross-encoder idea

Everything so far compares a query vector to *pre-computed* document vectors. The
document was embedded before it ever saw your query. This is a **bi-encoder**: two
independent encodings, compared at the end.

```
   query ──▶ [encoder] ──▶ vector ─┐
                                   ├──▶  cosine similarity
   doc   ──▶ [encoder] ──▶ vector ─┘     (doc encoded in advance)
```

That's what makes it fast — you index once and query forever. It's also what limits
quality: the document's vector can't emphasise whatever your query is about, because it
didn't know.

A **cross-encoder** feeds the query and the document into a model *together*, so the
model can attend to both at once:

```
   (query, doc) ──▶ [model] ──▶ relevance score
```

Much better. Also much slower — you can't precompute anything, so it's one model call per
document. Which is why it goes *last*, on a small pool of already-good candidates.

Continuum's reranker uses an LLM as the cross-encoder:

```python
_RERANK_SYSTEM = (
    "You are a relevance scoring assistant for a novel-knowledge retrieval system. "
    "Given a query and a list of passages, score each passage from 0 to 10 by how "
    "relevant it is to answering the query. Respond ONLY with valid JSON of the "
    "form {\"scores\": [<int>, <int>, ...]} with exactly one score per passage, "
    "in the same order as the input passages."
)
```

Ten passages per call, snippets truncated to 600 characters, temperature 0. And it fails
soft:

```python
except Exception as exc:
    logger.warning("LLMReranker call failed (%s); falling back to original order", exc)
    return candidates[:top_k]
```

If the model is down, you get the fused ordering — worse, not broken.

There's also a length-mismatch guard, because models miscount:

```python
if len(scores_obj) < expected:
    scores_obj = list(scores_obj) + [0.0] * (expected - len(scores_obj))
elif len(scores_obj) > expected:
    scores_obj = scores_obj[:expected]
```

Ask for ten scores, get nine, pad with zero. Get eleven, truncate. The scores are matched
to passages **positionally**, so a length mismatch would silently misalign every score
after the gap — the ninth passage getting the tenth's score. Padding is not elegant; it
is correct.

**In production, reranking is off.** The wiki's search endpoint calls
`retrieve(use_rerank=False)`. The cost — an LLM call on the read path — didn't justify
the gain for this workload, and it violates the Post 1 principle that reads should be
LLM-free. The component exists, works, and is available; it just isn't in the default
path. That's a legitimate outcome for a piece of infrastructure, and worth naming as one
rather than pretending everything built gets used.

### MMR: stop returning the same thing five times

Final stage. Suppose the top five results are:

```
1. chapter 3, scene 2:  Toren explains the lantern opens the Undervault
2. chapter 3 summary:   Toren explains the lantern opens the Undervault
3. chapter 3, event:    Toren reveals the lantern is a vault-key
4. chapter 3, scene 1:  Mira brings the lantern to the Glass Archive
5. chapter 3 (long):    Toren explains the lantern opens the Undervault
```

All highly relevant. All *the same information*. If you're assembling context for a model
with a limited budget, four of those five slots are wasted.

**Maximal Marginal Relevance** fixes this by scoring each candidate on relevance *minus*
its similarity to what you've already picked:

```
MMR(d) = λ · relevance(d) − (1 − λ) · max similarity(d, s) for s already selected
```

λ = 0.7 in Continuum: 70% weight on relevance, 30% penalty on redundancy. Select greedily,
recomputing the penalty each round.

```python
while remaining and len(selected) < top_k:
    for idx, cand in enumerate(remaining):
        rel = rel_cache[cand.item_id]
        max_sim_selected = 0.0
        for sel in selected:
            sim = _cosine(item_embeddings.get(cand.item_id), item_embeddings.get(sel.item_id))
            max_sim_selected = max(max_sim_selected, sim)
        mmr_score = lambda_ * rel - (1.0 - lambda_) * max_sim_selected
        ...
```

The first pick is pure relevance (nothing selected yet). Every later pick is asking
"what's the best thing I don't already have?"

And a small honest detail: a candidate with no stored embedding is treated as maximally
novel (`sim = 0.0`), so it competes on relevance alone rather than being penalised for
something we can't measure.

---

## The three bugs that made "hybrid" not hybrid

This is the part of the post that matters most, because it's the part you can't get from
a textbook. All three were found in August 2026, all three had been shipping, and all
three produced *plausible output* the entire time.

### Bug 1: MMR threw away the hybrid signal

Look again at the MMR formula. It needs a `relevance` number per candidate. Where does
that come from?

The original implementation computed it as **cosine similarity between the candidate's
embedding and the query embedding.**

Which sounds fine. It is a relevance score. It's just *the dense score*, recomputed from
scratch, discarding the fused rank that four stages of pipeline had carefully produced.

```
   BM25 ──┐
          ├──▶ RRF ──▶ (fused rank) ──▶ MMR ──▶ ✗ ignores it, recomputes dense cosine
   dense ─┘                                     ──▶ final order = dense-only
```

Every one of BM25's contributions — the exact name matches, the rare-term hits, the
entire reason for building a hybrid retriever — was recomputed away in the last stage.
The system was a dense retriever wearing a hybrid retriever's plumbing.

The observable symptom, from the fix notes: **the one result BM25 and dense both agreed
on landed 7th out of 8.** Agreement between independent signals is the strongest evidence
you have. It was being actively discarded.

The fix, and the docstring that now explains it so nobody re-introduces it:

```python
"""Relevance is the candidate's incoming `score` -- the fused (or reranked)
hybrid signal -- max-normalized to [0, 1] so it shares a scale with the
cosine novelty penalty. Recomputing relevance from the query embedding
instead would discard the keyword half of the hybrid and collapse the
ranking to dense-only.

Embeddings are used solely for the diversity term; a candidate without one
is treated as fully novel, and its relevance is on the same normalized
scale as everyone else's.
"""
```

Note the division of labour it establishes: **embeddings are used for the diversity term
only.** Relevance comes from the fusion. Novelty comes from the vectors. Each signal does
the job it's good at.

### Bug 2: two scales in one formula

Related, and it's an arithmetic bug hiding inside a modelling bug.

The MMR formula subtracts a cosine similarity (range [0, 1]) from a relevance score. So
relevance must also be in [0, 1] or the subtraction is meaningless.

RRF scores are *not* in [0, 1]. Fusing six lists gives values around
`6 × 1/61 ≈ 0.098` at best, and typically far less — around **0.016** for a single-list
hit.

So:

```
candidate WITH an embedding:     rel ≈ 0.6   (cosine)
candidate WITHOUT an embedding:  rel ≈ 0.016 (raw RRF)
```

A 37× difference produced by *which code path computed the number*, not by relevance.
Candidates without embeddings were unrankable — permanently last, regardless of how well
they matched.

Fix: max-normalise everything to a shared scale before combining.

```python
max_score = max((float(c.score) for c in candidates), default=0.0)
rel_cache = {
    c.item_id: (float(c.score) / max_score if max_score > 0.0 else 0.0)
    for c in candidates
}
```

> **If you're combining two numbers with an arithmetic operator, they must be on the same
> scale — and you should be able to say what the scale *is*.** "Roughly 0 to 1" isn't
> good enough when one of them is roughly 0 to 0.098.

There's a third, smaller fix in the same family: MMR now writes the MMR score back into
the result's `score` field, so the emitted order and the emitted numbers agree.
Previously the UI showed `0.02` for every row (the raw RRF score) in an order determined
by something else entirely — which looks, to a user, like the scores are meaningless.
They were.

### Bug 3: AND semantics — already covered

The `plainto_tsquery` bug from earlier in this post. 0 of 27 events matched.

### What made all three invisible

Every one of these produced *plausible* output. Search returned results. They were
related to the query. Nothing crashed, nothing logged, no test failed.

And here's the part that stings: **there was an eval for retrieval quality, and it
passed.**

The eval measured mean recall@8 against ten golden queries with a floor of **0.5**. The
buggy system scored **exactly 0.5**. It passed.

Why was the floor 0.5? Because it had been set by *measuring the system* and picking a
number just under it. That's the natural thing to do, and it is a trap: a floor
calibrated against broken behaviour cannot detect that behaviour.

The fix, in the test file:

```python
# Raised 0.5 -> 0.9 on 2026-08-14. The old floor was set while BM25 used
# AND semantics and MMR reranked on query-embedding cosine, which scored
# exactly 0.5; leaving it there would let that regression back in silently.
# Full-recall queries score 1.0, so 0.9 tolerates one query slipping.
assert report["mean_recall_at_k"] >= 0.9, report["per_query"]
```

After the fixes, it scores **1.00**.

> **A threshold set by observing current behaviour is a regression detector, not a
> quality bar.** It tells you "no worse than the day I wrote it" — and if that day was a
> bad day, it enforces the bad day forever. Derive thresholds from what the system
> *should* do, then investigate the gap.

---

## Two more things that were quietly wrong

### Ties were nondeterministic

`ts_rank_cd` produces a lot of exactly-equal scores, especially on short documents. The
BM25 query ordered by score only:

```sql
ORDER BY raw_score DESC
```

With ties, Postgres returns rows in whatever order it likes — which can vary run to run
depending on plan and physical layout. And RRF consumes *rank*, so an arbitrary tie order
propagates straight into the fused ranking. The eval would pass on Tuesday and fail on
Wednesday with no code change and no seed to reproduce it.

```sql
-- item_id breaks ties deterministically: ts_rank_cd produces many
-- exactly-equal scores, and RRF consumes rank alone, so an arbitrary
-- tie order propagates straight into the fused ranking and makes the
-- retrieval eval flaky with no seed to reproduce a failure.
ORDER BY raw_score DESC, item_id
```

One extra column. Not "more correct" ranking — *reproducible* ranking, which is a
prerequisite for measuring anything.

### A dead dense stage was invisible

The retriever runs BM25 and dense in parallel and catches per-stage failures so one
broken stage doesn't kill the request:

```python
try:
    res = fut.result()
except Exception as exc:
    res = []
per_kind.setdefault(kind, {})[source] = res
```

Sensible. But the failure was recorded only in a `debug` dict that the calling code
throws away. So if the embedding provider was down, every search silently became
BM25-only, returned 200 OK, and looked fine.

```python
except Exception as exc:
    # debug is discarded by reads/search.py, so without this log
    # a fully-offline dense stage is invisible: the request
    # still returns 200 with BM25-only results.
    logger.warning("retrieval stage %s/%s failed, continuing degraded: %s",
                   source, kind, exc)
    debug.setdefault("errors", []).append({"stage": source, "kind": kind, "error": str(exc)})
```

> **Graceful degradation without a signal is just silent failure.** If a system can run
> in a degraded mode, something must be able to tell you that it is.

---

## The cutoff, threaded all the way down

One thing I've deliberately left in the background: every query in this post carries the
chapter cutoff from Post 1.

```sql
WHERE c.novel_id = %(novel_id)s
  AND c.search_tsv @@ tq.query
  AND (%(max_chapter)s::int IS NULL OR c.number <= %(max_chapter)s::int)
```

It's in the BM25 SQL, in the dense SQL, in the scene and event and commitment variants.
It has to be, and it has to be *in the SQL* rather than applied afterward — because if you
filter after retrieval, your top-50 candidate pool is full of future chapters that get
dropped, and you're left with eight results when you asked for fifty. The filter must
happen before the `LIMIT`, which means it must happen in the database.

This is the discipline Post 9 makes structural, with a test that fails the build if any
read function forgets it.

---

## Honest reporting: what the number actually measures

One last thing, and it's about intellectual honesty in evaluation.

The retrieval eval runs offline, with no API key, using **mock embeddings** — SHA-256
hashes of the text, deterministically expanded to 1536 floats. That's what lets the eval
run in CI for free.

But hash vectors carry *no semantic signal whatsoever*. Two strings differing only by a
trailing period measure about **−0.016** cosine against each other; two completely
unrelated texts measure about **−0.001**. Similar texts are, if anything, slightly
*further apart* than unrelated ones. It's noise.

And that noise is fused into RRF as three of six ranked lists, **at equal weight**. So
the offline number isn't "hybrid retrieval quality." It's BM25 recall, degraded by a
random channel.

For a while the eval reported it as `mean_recall_at_k` — a name that invites being quoted
as "our retrieval scores 1.0." The fix was to make the metric name carry the caveat:

```python
metric = "hybrid_recall_at_k" if use_real_embeddings else "bm25_recall_at_k"
return {
    "metric": metric,
    metric: mean,
    "dense_channel": "real" if use_real_embeddings else "hash (noise)",
    ...
}
```

And a test whose entire job is to prevent the misleading label from coming back:

```python
def test_offline_metric_is_labelled_bm25_not_hybrid(db, golden_novel):
    """The offline number must not present itself as hybrid retrieval quality.
    ... Reporting it as hybrid recall overstates what was measured; the label is
    the guard against that number being quoted.
    """
    report = run_retrieval_eval(db, golden_novel, k=8)
    assert report["metric"] == "bm25_recall_at_k"
    assert "hybrid_recall_at_k" not in report
    assert report["dense_channel"] == "hash (noise)"
```

A unit test that enforces honesty about what a metric means. I've not seen that
elsewhere, and I think it should be more common.

---

## Where we are

We can look things up exactly (Post 5) and find them fuzzily (this post), both with a
chapter cutoff.

But everything so far is passive. You have to *ask*. Nothing in the system has ever
looked at a chapter and said "wait — that's wrong."

That's next.

---

*Next: [Part 8 — The Critic](08-the-critic.md) — how to automatically catch a continuity
error, why "ask a model if anything looks off" is the wrong shape of solution, and the
audit that discovered three of five checks were structurally incapable of ever firing.*
