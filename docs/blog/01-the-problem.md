# Part 1 — The Novel That Won't Fit In Anyone's Head

*This is the first post in a series about building **Continuum**, a memory system for
long stories. The series starts from nothing — no assumed knowledge of databases,
search, or AI systems — and ends with a complete, working, measured piece of
infrastructure. If you can read a Python function, you have enough background.*

---

## A scene

You are writing a novel. It's long — say four hundred thousand words across seventy
chapters, which is unremarkable for epic fantasy and about four times a typical literary
novel. You have been writing it for two years.

In chapter 4, a character named Aelric picks up a silver dagger off a dead soldier.
You mention that the dagger has a chipped crossguard. Three sentences, easy to miss.

In chapter 31, you write a fight scene. Aelric draws the dagger.

Here is the question that this entire series exists to answer: **does he still have
it?**

You wrote chapter 12, in which he gave the dagger to his sister for safekeeping. You
wrote that eight months ago. You've written 180,000 words since. You do not remember.

So you do what every novelist does: you open the manuscript and press Ctrl-F. You
search for "dagger". You get 94 hits. You start reading them.

That's the problem. Not a hard problem, not an interesting problem — just a slow,
grinding, unavoidable one. And it gets worse in exactly the way you'd expect: the
longer the book, the more facts there are to lose track of, and the further back they
are.

Now change one thing. Instead of a human writing chapter 31, imagine an AI writing it.

The problem doesn't get easier. It gets *much* harder, and it gets harder for reasons
that are worth understanding in detail, because those reasons determine everything
about the system we're going to build.

---

## What an LLM actually is, for our purposes

Let's fix some vocabulary, because I'm going to use these words constantly and I don't
want you guessing.

A **Large Language Model** (LLM) — GPT-4, Claude, Gemini — is, from the outside, a
function. You hand it text. It hands you back text. That's the whole interface:

```python
output_text = model(input_text)
```

Everything else is detail. Important detail, but detail.

The text you hand it is called the **prompt**. The text it hands back is called the
**completion**. The model has no memory between calls. If you call it twice, the
second call knows nothing about the first unless you paste the first conversation into
the second prompt yourself. This is not a limitation you can configure away; it is what
the thing is. An LLM is a pure function of its input.

That single fact — *no memory between calls* — is the seed of this entire project. Sit
with it for a second. If a model is writing chapter 31 of your novel, then whatever it
knows about chapters 1 through 30 has to be *in the prompt*, because there is nowhere
else for it to be.

### Tokens

The model doesn't see letters or words. It sees **tokens**, which are chunks of text
somewhere between a character and a word. The tokenizer that OpenAI's models use
(called `cl100k_base`, and yes we will use it later) splits text roughly like this:

```
"The dagger had a chipped crossguard."
   ↓
["The", " d", "agger", " had", " a", " chipped", " cross", "guard", "."]
   9 tokens
```

Common words are one token. Rare words split into pieces. Punctuation is usually its
own token. The rule of thumb for English prose is:

> **1 word ≈ 1.33 tokens**

That number is going to do a lot of work in a minute, so remember it. Four hundred
thousand words of novel is about 530,000 tokens.

### The context window

Here is the constraint that matters. Every model has a **context window**: the maximum
number of tokens it can accept in one call. Not a soft limit — a hard one. Exceed it and
the API returns an error, not a degraded answer.

Context windows have grown a lot. A few years ago 4,000 tokens was normal. Now models
with 128,000 or 200,000 or even a million tokens exist. So the obvious thought is:

> "Fine — models keep getting bigger. Just paste the whole novel into the prompt and
> the problem goes away."

Let's take that seriously, because it's the right first instinct and understanding
exactly why it fails is most of the education.

---

## Attempt 1: paste the whole book

Our novel is 530,000 tokens. Let's suppose we have a model with a 200,000-token
context window, which is a real and generous size as of this writing.

**It doesn't fit.** That's not a subtle argument. 530,000 > 200,000. We are done before
we start.

But suppose you're writing a shorter book — 150,000 words, 200,000 tokens. Now it
technically fits. Let's push on it anyway, because three separate things go wrong, and
each one teaches us something we'll need.

### Problem A: it costs money, every single time

LLM providers charge per token. Prices change constantly, so let's do the arithmetic
symbolically and then plug in a number.

Suppose input tokens cost **$X per million tokens**. Writing one chapter with the whole
book in context costs:

```
cost per chapter = 200,000 tokens × X / 1,000,000 = 0.2 × X dollars
```

At $X = $2.50 per million (a mid-range price for a capable model), that's **$0.50 per
call**. Sounds cheap!

But you don't call the model once per chapter. You call it to draft. Then you notice
the draft has a problem and call it again. Then again. Say five calls per chapter, and
you have 70 chapters:

```
5 calls × 70 chapters × $0.50 = $175
```

...for one pass through one book. And the book *grows* — by chapter 70 the context is
much bigger than it was at chapter 5, so this is an underestimate. Not ruinous, but
notice the shape of it: **you are paying, over and over, to re-read a book that hasn't
changed.** That's a pure waste, and it scales quadratically with book length (each
chapter costs proportional to the book so far, summed over all chapters ≈ n²/2).

Hold onto that observation. It becomes the central architectural bet of this project.

### Problem B: models don't read the middle

This one is empirical and slightly unsettling. Researchers have repeatedly found that
when you put a lot of text in a model's context and then ask a question whose answer is
buried in it, the model is much better at using information near the *beginning* or the
*end* of the context than information in the *middle*. The effect is well-documented
enough to have a name — "lost in the middle."

Draw it as a rough performance curve against where the fact sits:

```
 accuracy
    ▲
1.0 │██                                          ██
    │███                                        ███
    │████                                      ████
0.5 │ ████                                    ████
    │   ██████                          ██████
    │       ████████████████████████████
0.0 │
    └────────────────────────────────────────────────▶
     start          middle of context            end
```

Your dagger fact from chapter 4 is, in a 70-chapter book, almost exactly in the
worst possible place. You have technically supplied the information. The model has
technically received it. It will still get it wrong at a meaningful rate.

### Problem C — the fatal one: it doesn't let you *check*

Suppose we solved cost and we solved lost-in-the-middle. We hand the model the whole
book and ask for chapter 31. It produces a chapter. Aelric draws the silver dagger.

**Is that right or wrong?**

You have no idea. The model didn't tell you which facts it relied on. There's nothing
to query. You can ask a second model to read the chapter and check it, but that just
moves the problem: now you have two models with the same blind spots, and neither of
them can give you a straight answer to "as of chapter 31, who is holding the silver
dagger?"

This is the deepest point in this post, so I'll say it as plainly as I can:

> **A pile of prose is not a queryable thing.** You can generate from it. You cannot
> interrogate it, audit it, or prove anything about it.

Every fix in this series flows from wanting to turn the prose into something you can
ask questions of.

---

## What actually goes wrong (the failure taxonomy)

Before we design anything, let's get specific about what "continuity errors" actually
are. Vague problems get vague solutions. Here are the real categories, with an example
of each from our imaginary novel.

**1. Object-state errors.** Aelric draws a dagger he gave away nineteen chapters ago.
Someone drinks from a cup that shattered in chapter 6.

**2. Location errors.** Kessa is in the Saltmarsh in the same scene where she is also
described as being in the capital. Or she travels between two cities in an afternoon
that the book earlier established as a week's ride apart.

**3. Attribute contradictions.** Chapter 2 says Aelric has grey eyes. Chapter 40 says
brown. Nothing in between explains the change.

**4. Knowledge errors — the nasty one.** In chapter 22, Kessa says "I know your brother
betrayed you." But Kessa was never told this. She wasn't in the room in chapter 18 when
it was revealed. She has no way to know it, and the reader, who *was* in that room,
notices immediately.

**5. Broken promises.** Chapter 7 makes a great deal of a locked door in the cellar
that nobody can open. The book ends. The door is never opened. The reader feels
cheated. This has a name in craft circles — Chekhov's gun, the principle that a rifle
on the wall in act one must be fired by act three.

**6. Thread abandonment.** A subplot — the missing archivist, say — is set up in
chapter 9, advanced in chapter 15, and then simply never mentioned again.

Now, which of those is hardest?

There's actual evidence on this. A 2026 benchmark called ConStory-Bench evaluated
long-form story generation from major models and found that the two dominant error
categories were **factual/detail consistency** (categories 1–3 above) and
**timeline/plot logic** (categories 5–6). Not prose quality. Not creativity. Bookkeeping.

And knowledge errors — category 4 — turn out to be the worst of all. A separate
benchmark family (KNP/IKD) tested how well models track *who knows what* in a
narrative. The best model tested reached **69%**. Humans score **92%**. One
widely-used model performed **at chance** — that is, no better than flipping a coin.

Read that again, because it's the empirical justification for a lot of engineering
that would otherwise look like overkill:

> Models are, right now, roughly as good at tracking who-knows-what as a coin flip is,
> and at best 23 points below a human. If you want that tracked correctly, **you have
> to track it yourself, outside the model.**

That sentence is the thesis of this whole project.

---

## Attempt 2: search the book instead of pasting it

OK. Pasting the book is out. The obvious next idea, and the one most people reach for,
is: don't send the whole book — send only the *relevant parts*.

This pattern has a name: **RAG**, for Retrieval-Augmented Generation. It sounds fancy;
it is not. It is three steps:

```
1. RETRIEVE   — search a pile of documents for the bits related to your question
2. AUGMENT    — paste those bits into the prompt
3. GENERATE   — call the model
```

That's it. That's RAG. It's Ctrl-F with extra steps and a better matching algorithm.

For our case, we'd cut the novel into pieces — say, one piece per scene — and store
them. When the model is about to write chapter 31 and the scene involves Aelric and a
dagger, we search for "Aelric dagger", pull the top ten matching passages, and paste
those into the prompt instead of the whole book.

This is a genuine improvement. It fixes cost (ten passages, not seventy chapters) and
it fixes lost-in-the-middle (a small prompt has no middle to get lost in). Many
production systems stop here.

For a novel it is **catastrophically wrong**, in two specific ways.

### Failure 1: naive retrieval leaks the future

Here is the sequence of events. The model is drafting chapter 3. It wants to know about
Aelric's sister, so the system searches the book for "Aelric's sister."

The best match in the entire manuscript, by any measure of relevance you like, is
chapter 44 — the chapter where Aelric's sister is revealed to be the antagonist and
dies.

The system dutifully pastes chapter 44 into the prompt.

The model, writing chapter 3, now knows how the book ends.

```
   ┌──────────────────────────────────────────────────────────┐
   │  THE MANUSCRIPT                                          │
   │                                                          │
   │  ch1  ch2 [ch3] ch4 ... ch20 ... ch44 ... ch70           │
   │            ▲                     │                       │
   │            │   we are writing    │  best keyword match   │
   │            │      here           │  lives here           │
   │            └─────────────────────┘                       │
   │                    SPOILER                               │
   └──────────────────────────────────────────────────────────┘
```

You might think: "so what, it's the same author, they already know the ending." But
that misses it. If the model has chapter 44 in context while writing chapter 3, chapter
3 comes out *shaped* by chapter 44 — foreshadowing that shouldn't exist yet, characters
who act like they already suspect. And if the writer is an autonomous agent working
through the book in order, this is fatal: it will write chapter 3 as though the reveal
has already happened, because as far as its context is concerned, it has.

A retrieval system over a novel must be able to answer questions **as of a point in
time**, and refuse to return anything later. Nothing about standard RAG does this. We
are going to have to build it, and it is going to constrain the design of every single
component in the system.

I'm going to call this property **spoiler-safety**, and the mechanism that provides it
the **cutoff**. Both terms will recur constantly.

### Failure 2: text has no idea what's still true

This one is subtler and, in my opinion, more interesting.

Suppose our chunk store contains these two passages:

- From chapter 4: *"Aelric took the silver dagger from the dead man's belt."*
- From chapter 12: *"Take it," he said, pressing the dagger into Mira's hands. "Keep it
  safe."*

Now we search for "who has the silver dagger". Both passages match, strongly. Both get
pasted into the prompt. Both are, as sentences, completely true.

**And the answer to our question is neither of them.** The answer is a *relationship
between* them: the second one supersedes the first. That relationship is nowhere in the
text. It exists only in the fact that 12 > 4.

Standard RAG returns text, ranked by similarity to a query. Similarity has no opinion
about time. A search engine will happily hand you a fact and its own refutation, side by
side, with no signal about which won.

Here's a way to see the shape of the problem clearly. What we want to represent is not
a set of sentences. It's a **timeline of who held the thing**:

```
       ch1   ch4      ch12                       ch70
        │     │        │                          │
Aelric  │     ├────────┤                          │
        │     ▲        ▲                          │
        │   gained    lost                        │
        │              │                          │
Mira    │              ├──────────────────────────▶
        │              ▲                          │
        │            gained          (still holds it)
```

Given that picture, "does Aelric have the dagger in chapter 31?" is not a search
problem at all. It's a lookup: find the interval that covers chapter 31. The answer is
Mira, instantly, with certainty, for free.

But you can only do that lookup if you have *built the picture*. And you can only build
the picture if you've turned prose into structure.

---

## The actual insight

Put the two failures together and you get the shape of the answer.

The reason naive retrieval fails on novels isn't that the search is bad. It's that
**text is the wrong data structure for the question being asked.** Questions like:

- Who holds the dagger as of chapter 31?
- What does Kessa know as of chapter 22, and how did she learn it?
- Which promises made before chapter 40 are still unpaid?
- Which characters were in the Saltmarsh at the same time?

...are *database* questions. They have exact answers. They want rows and columns and
time intervals, not paragraphs ranked by vibes.

So here's the plan, stated in one sentence:

> **Read each chapter once, expensively, and turn it into structured facts. Then answer
> every future question cheaply, exactly, and with a time filter, from the structure.**

Let's unpack that, because there are three separate commitments in there.

**"Read each chapter once, expensively."** We're going to spend a *lot* of LLM calls per
chapter — the finished system spends thirteen separate calls per chunk of text, plus
several more for cleanup. That sounds insane until you notice it happens exactly once
per chapter, ever, and is then amortized across every question anyone asks for the rest
of the book's life. Compare that to the "paste the book" approach, which pays a large
cost on *every single query, forever*. We are moving cost from the read path to the
write path. That's the bet.

**"Structured facts."** Not "a summary." A summary is still prose — still unqueryable.
We want rows: `(Aelric, holds, silver dagger, from chapter 4, until chapter 12)`. A
thing a computer can filter, join, and count.

**"With a time filter."** Every fact carries the chapter it came from. Every query
carries a cutoff. Nothing later than the cutoff is ever returned. This is not a feature
bolted on at the end — it will be a structural property enforced by a test that walks
the codebase and fails the build if any function forgets it. (Post 9.)

Here's the whole system in one picture. Don't worry about the individual boxes yet;
each one gets its own post.

```
        ┌──────────────────────┐
        │   raw chapter text   │   ← the only ground truth
        └──────────┬───────────┘
                   │
       ═══════════ │ ═══════════  expensive, once per chapter
                   ▼
        ┌──────────────────────┐
        │  LLM extraction      │   who, where, what changed,
        │  (13 focused passes) │   what was promised, what was learned
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │  identity resolution │   "Al" and "Ms. Vance" are one person
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │  Postgres            │   entities, events, an append-only
        │  (structured store)  │   change log, time-interval edges
        └──────────┬───────────┘
                   ▼
       ═══════════ │ ═══════════  cheap, every query, forever
                   ▼
        ┌──────────────────────┐
        │  reads, always with  │──▶ a wiki for humans
        │  a chapter cutoff    │──▶ a tool server for AI writers
        └──────────────────────┘
```

The line across the middle is the most important thing in the diagram. Above it: slow,
expensive, LLM-driven, runs once. Below it: fast, deterministic, SQL, runs constantly.
No LLM ever generates prose below that line. That separation is what makes the answers
*trustworthy* — a query for "who holds the dagger at chapter 31" doesn't ask a model to
think about it, it looks up a row.

---

## What this system is not

Two clarifications that will save you confusion later, because the project's own history
went through both of them.

**It doesn't write the novel.** Continuum reads chapters and answers questions about
them. Something else — a human, or an AI agent — does the writing. For about six weeks
in mid-2026 the project *did* contain a chapter-generation loop: a scene planner, a
prose drafter, a revise-until-the-critic-passes cycle. All of it was built, wired
together, and then deliberately deleted. Post 11 tells that story properly, including why
deleting working code was the right call.

**It's not a chatbot over your book.** There's no "ask my novel anything" text box that
pipes results into a model. The primary output is *structured data*: lists, rows,
graphs, intervals. A separate program can feed that to a model if it wants. Keeping the
structured layer free of generation is what makes it auditable.

---

## The route from here

Here is the road we're going to walk. Each post takes one problem, shows the naive
solution, breaks it with a real example, and then builds the real thing. Where the real
project got it wrong first, I'll show you the wrong version too, because the wrong
versions are usually more instructive than the right ones.

| Post | The question it answers |
|---|---|
| **2. From Prose to Rows** | How do you get from raw prose to rows in a database at all? Relational basics, chunking, prompting for JSON, and why six focused LLM calls beat one big one. |
| **3. Thirteen Passes** | What does the finished extractor actually ask for, pass by pass? Why each pass was added, which prompt rules are really bug fixes, and what a chapter costs. |
| **4. Identity** | How do you know "Al", "Alice", and "the woman from the tavern" are one person — and how do you avoid welding two people together, which is much worse? |
| **5. Time** | How do you answer "as of chapter 31" without storing 70 copies of the world? Event logs, replay, time intervals, and invalidate-don't-delete. |
| **6. Modelling a World** | Relationships that point both ways (or don't), entity types the schema was never designed for, five kinds of graph edge, and the two id spaces. |
| **7. Search** | Keyword search, vector search, why you need both, how to fuse them by rank, and three bugs that made "hybrid" search silently not hybrid. |
| **8. The Critic** | How do you *automatically catch* a continuity error, and why "ask a model if it looks wrong" is the wrong shape of answer? |
| **9. Serving It** | One read layer, two audiences (a human wiki, an AI agent), and a test that structurally forbids leaking the future. |
| **10. Measuring It** | How do you know any of this works? Golden datasets, four evals, and the threshold that certified a broken system as working. |
| **11. Production** | The unglamorous 20%: transactions, connection pools, indexes, a lock, a silent data-loss bug a green test suite could never catch, and deleting six weeks of working code on purpose. |

By the end you'll be able to defend every decision in a system with 419 tests, thirteen
LLM extraction passes, a bitemporal knowledge graph, hybrid search, five continuity
checks and four evals — and, just as importantly, explain the six things it still
gets wrong.

Let's go build it.

---

*Next: [Part 2 — From Prose to Rows](02-the-mvp.md), in which we write the first
version, learn just enough about databases to design a schema, and discover that asking
an LLM for one big JSON object is a worse idea than asking it six small ones.*
