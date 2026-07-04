from __future__ import annotations

import json
from textwrap import dedent


PASS_ORDER = [
    "chapter_summary",
    "new_entities",
    "entity_deltas",
    "events",
    "thread_updates",
    "continuity_flags",
    "relationship_updates",
    "dynamics_updates",
    "scene_segmentation",
    "multi_granularity_summaries",
    "knowledge_state_deltas",
    "commitments",
    "canon_facts",
]


PASS_SCHEMAS = {
    "chapter_summary": {
        "summary": "string (<= 300 tokens)",
    },
    "new_entities": {
        "characters": [
            {
                "name": "string",
                "aliases": ["string"],
                "description": "string",
            }
        ],
        "locations": [
            {
                "name": "string",
                "description": "string",
                "parent_location": "string|null  # canonical parent if this is a sub-location (room/floor/corridor); null for top-level locations",
            }
        ],
        "factions": [{"name": "string", "description": "string"}],
        "objects": [
            {
                "name": "string",
                "description": "string",
                "owner_name": "string|null  # character who owns/carries this object; null if unowned or unknown",
                "distinguishing_properties": "string|null  # brief note distinguishing this from similar objects (colour, markings, magic, etc.)",
                "significance": "string|null",
            }
        ],
    },
    "entity_deltas": {
        "character_deltas": [
            {
                "character_name": "string",
                "location": "string|null",
                "emotional_state": "string|null",
                "goals": "string|null",
                "knowledge": ["string"],
                "physical_state": "string|null",
                "appearance": "string|null — visible description (clothing, hair, distinguishing features) only when explicitly described",
                "notes": "string|null",
            }
        ]
    },
    "events": {
        "events": [
            {
                "description": "string",
                "event_type": "action|revelation|death|arrival|conflict|other",
                "impact_level": "low|medium|high|critical",
                "involved_characters": ["string"],
                "involved_locations": ["string"],
                "involved_objects": ["string"],
                "involved_factions": ["string"],
            }
        ]
    },
    "thread_updates": {
        "thread_updates": [
            {
                "title": "string",
                "description": "string",
                "status": "open|progressing|closed",
                "impact": "opens|advances|closes",
                "thread_type": "mystery|conflict|goal|prophecy|secret|other",
                "event_description": "string",
            }
        ]
    },
    "continuity_flags": {
        "continuity_flags": [
            {
                "description": "string",
                "flag_type": "foreshadowing|planted_detail|setup|callback|other",
            }
        ]
    },
    "relationship_updates": {
        "relationship_updates": [
            {
                "entity_a": "string",
                "entity_b": "string",
                "rel_type": "string",
                "from_chapter": "integer|null",
                "to_chapter": "integer|null",
                "notes": "string|null",
            }
        ]
    },
    "dynamics_updates": {
        "dynamics_updates": [
            {
                "entity_a": "string",
                "entity_b": "string",
                "description": "string",
            }
        ]
    },
    "scene_segmentation": {
        "scenes": [
            {
                "scene_index": "integer (0-based)",
                "pov_character_name": "string|null",
                "location_name": "string|null",
                "time_anchor": "string|null",
                "present_character_names": ["string"],
                "summary": "string (~50 words)",
                "starts_at_excerpt": "string (~30 chars verbatim from chapter text where the scene begins)",
            }
        ]
    },
    "multi_granularity_summaries": {
        "summary_short": "string (~50 chars, one sentence)",
        "summary_medium": "string (~150 words, one paragraph)",
        "summary_long": "string (~500 words)",
    },
    "knowledge_state_deltas": {
        "learnings": [
            {
                "character_name": "string",
                "fact_description": "string (what they learned)",
                "source_type": "dialogue|observation|inference|witnessed|told|assumed",
                "source_character_name": "string|null",
                "certainty": "high|medium|low",
                "shared_with_character_names": ["string"],
            }
        ]
    },
    "commitments": {
        "foreshadows_introduced": [
            {
                "foreshadow_text": "string (~1-2 sentences describing what was planted)",
                "trigger_predicate": "string (the condition under which this should pay off)",
                "weight": "low|medium|high",
                "related_entity_names": ["string"],
            }
        ],
        "payoffs_delivered": [
            {
                "payoff_text": "string (what was paid off this chapter)",
                "matches_foreshadow": "string (text of the original foreshadow if identifiable, else empty)",
            }
        ],
    },
    "canon_facts": {
        "canon_facts": [
            {
                "subject_name": "string  # entity the fact is about, exactly as named in the chapter",
                "subject_type": "character|location|object|faction",
                "predicate": "string  # stable snake_case key, e.g. eye_color, home_town, weapon, title, sibling_of",
                "value": "string  # the fact's value, concise",
                "kind": "physical|relational|world_rule|backstory|other",
                "confidence": "number 0.0-1.0",
                "quote": "string  # verbatim supporting sentence from the chapter",
            }
        ]
    },
}


def build_context_block(context: dict) -> str:
    return dedent(
        f"""
        STORY CONTEXT (summarized JSON)
        {json.dumps(context, ensure_ascii=True, default=str)}
        """
    ).strip()


def build_system_prompt(pass_name: str, custom_entity_types: list[dict] | None = None) -> str:
    schema = PASS_SCHEMAS[pass_name]
    if pass_name == "new_entities" and custom_entity_types:
        schema = dict(schema)
        schema["custom_entities"] = [
            {
                "name": "string",
                "type": " | ".join(t["name"] for t in custom_entity_types),
                "description": "string",
            }
        ]
    return dedent(
        f"""
        You are an extraction engine for a novel continuity pipeline.
        Return only strict JSON. No prose, no markdown.
        Keep facts grounded in provided text.

        Extraction pass: {pass_name}
        Required output schema:
        {json.dumps(schema, ensure_ascii=True, indent=2)}
        """
    ).strip()


PASS_TASK_INSTRUCTIONS: dict[str, str] = {
    "new_entities": dedent(
        """
        Extract ONLY entities that are genuinely new — not already present in the
        STORY CONTEXT above.

        LOCATIONS
        - Use the canonical, top-level name for a place (e.g. "Corporate Office",
          "Jake's Apartment"). Never create a sub-location row for a room, floor,
          corridor, or fixture that lives inside an already-established location.
        - If the scene happens inside a sub-location (14th floor, elevator, back room),
          set name to the PARENT location and leave parent_location null.
        - If you must distinguish the sub-location (e.g. a named room important to the
          plot), set name to that specific name AND set parent_location to the canonical
          parent (e.g. parent_location="Corporate Office").
        - Name variants of the same place ("Jake's home", "Jake's flat", "Jake's
          apartment") are the SAME location. Pick one canonical name; do not emit both.
        - If a location already exists in STORY CONTEXT under any alias or close variant,
          do NOT create a new entry.

        OBJECTS
        - Extract physical items only: weapons, armor, tools, artifacts, equipment,
          and named possessions with narrative significance.
        - DO NOT extract skills, abilities, spells, character classes, stat windows,
          or system notifications as objects — these are game mechanics with no
          physical form.
        - Set owner_name to the character who owns, carries, or is specifically
          associated with this object. Two characters can each have "a black sedan" —
          they are DIFFERENT objects; give each one a distinct name that includes the
          owner (e.g. "Jake's black sedan", "Sarah's black sedan").
        - Set distinguishing_properties to any brief descriptor that makes this object
          unique (colour, damage, inscriptions, enchantments, etc.).
        - Generic props with no identity (a glass of water, a chair) should NOT be
          extracted as objects unless they recur or carry narrative significance.

        CHARACTERS
        - Extract only sentient beings who act in the story: people, named NPCs,
          monsters, and creatures that interact with the protagonist.
        - DO NOT extract game-system elements as characters. This includes:
          character classes (e.g. "Archer Class", "Mage Class"), skills, abilities,
          spells, stat windows, tutorial screens, system notifications, or any
          mechanic that is not a being. If it cannot think, speak, or act of its
          own will, it is NOT a character.
        - Only extract if not already in STORY CONTEXT.

        FACTIONS
        - Standard rules: only extract if not already in STORY CONTEXT.

        Return JSON only.
        """
    ).strip(),
    "relationship_updates": dedent(
        """
        Extract relationships between entities. Each row must have exactly one
        relationship type — never combine multiple types with "/" or ",".
        If two entities have more than one distinct relationship, emit a separate
        row for each (e.g. one row for "mentor", another for "father").

        Use lowercase, specific labels (e.g. "rival", "employer", "romantic_interest")
        rather than vague or compound ones ("guide/subject", "friends/colleagues").

        For relationships that are inherently mutual (e.g. "spouse_of", "sibling_of",
        "friend_of", "rival_of"), emit exactly one row for the pair — not one row per
        direction. Reserve two separate rows only for relationships that genuinely
        differ by direction (e.g. "mentor" vs. "employer" between the same two entities).
        Return JSON only.
        """
    ).strip(),
    "scene_segmentation": dedent(
        """
        Segment the chapter chunk into discrete scenes. A scene is a continuous
        unit of action sharing the same time, place, and (usually) POV. A new
        scene begins on a hard break: location change, time jump, POV switch,
        explicit scene divider, or a clear shift of focus.

        For each scene return:
          - scene_index: 0-based ordinal within this chunk
          - pov_character_name: the viewpoint character if identifiable, else null
          - location_name: where the scene takes place if identifiable, else null
          - time_anchor: free-text time hint ("dawn", "the next morning",
            "three days later") if present, else null
          - present_character_names: characters who appear in the scene
          - summary: ~50-word neutral summary of what happens
          - starts_at_excerpt: a verbatim substring of about 30 characters
            from the chapter text marking where the scene starts

        Prefer fewer, well-defined scenes over over-segmentation. If the entire
        chunk is one scene, return a single entry with scene_index=0.
        Return JSON only.
        """
    ).strip(),
    "multi_granularity_summaries": dedent(
        """
        Produce three summaries of the chapter chunk at different granularities:
          - summary_short: a single sentence (~50 chars) capturing the core beat.
          - summary_medium: one paragraph (~150 words) covering the major events,
            character beats, and any reveals.
          - summary_long: a detailed ~500-word recap suitable for a continuity
            assistant. Include character names, locations, and key dialogue beats.

        All three must remain grounded in the provided text. Do not invent.
        Return JSON only.
        """
    ).strip(),
    "knowledge_state_deltas": dedent(
        """
        Extract what each character LEARNS during this chunk — new information
        they did not have before. Only include knowledge clearly acquired in this
        chunk, not pre-existing knowledge.

        For each learning:
          - character_name: who acquires the knowledge
          - fact_description: concise statement of what they now know
          - source_type: how they came by it. Use exactly one of:
              dialogue (told in conversation),
              observation (saw it directly),
              inference (deduced from other facts),
              witnessed (saw an event happen),
              told (received an explicit telling),
              assumed (taken on belief without evidence)
          - source_character_name: who told them, if applicable; else null
          - certainty: high | medium | low
          - shared_with_character_names: other characters present who also
            acquire this knowledge in the same beat

        Skip background recollections. Skip the omniscient narrator's knowledge.
        Return JSON only.
        """
    ).strip(),
    "commitments": dedent(
        """
        Identify foreshadow / payoff structure (Chekhov's gun, narrative debt).

        foreshadows_introduced: things planted in this chunk that imply a future
        payoff. For each, give:
          - foreshadow_text: ~1-2 sentence description of what was planted
          - trigger_predicate: a short clause stating when/how this should pay
            off ("when the protagonist confronts the antagonist",
            "when the locked door is opened")
          - weight: low | medium | high — how central this is to the story
          - related_entity_names: characters, objects, or locations tied to the
            foreshadow

        payoffs_delivered: things in this chunk that fulfill an earlier setup.
          - payoff_text: what was paid off in this chunk
          - matches_foreshadow: verbatim or paraphrased text of the foreshadow
            it satisfies, if identifiable; empty string otherwise.

        Be conservative. Do not flag everyday descriptions. Return JSON only.
        """
    ).strip(),
    "canon_facts": dedent(
        """
        Extract DURABLE, objective facts that future chapters must not
        contradict — physical traits (eye_color, hair_color, height), fixed
        relations (sibling_of, parent_of), origins (home_town, birthplace),
        possessions with identity (signature weapon), and hard world rules
        (magic costs, physical laws of the setting).

        Rules:
        - predicate must be a stable snake_case key; reuse common predicates
          (eye_color, hair_color, title, home_town, weapon, sibling_of,
          parent_of, species, age) rather than inventing synonyms.
        - Only facts explicitly stated or unambiguously shown in this chunk.
        - SKIP transient state (mood, current location, temporary injuries),
          opinions, and speculation. Those belong to other passes.
        - quote must be a verbatim sentence from the chunk supporting the fact.
        - confidence: 1.0 for directly stated, lower for strongly implied.

        Return JSON only.
        """
    ).strip(),
    "continuity_flags": dedent(
        """
        Flag ONLY narrative elements that must pay off in a future chapter or would
        create a plot hole / broken promise if forgotten. Ask: "If the author never
        references this again, would a careful reader feel cheated?"

        Flag:
        - Character abilities, skills, or traits explicitly established for later use
        - Backstory (trauma, rivalries, history) that will plausibly drive future choices
        - Introduced objects whose special properties haven't been exercised yet
        - Explicit foreshadowing — stated predictions, ominous hints, prophecies
        - Open promises, threats, oaths, or stated goals not yet pursued
        - Unresolved mysteries or questions the narrative implicitly promises to answer

        Do NOT flag:
        - Mechanical scene transitions (timers, transport messages, system notifications)
        - World-building facts that are informational but carry no narrative debt
        - Events or setups that are fully resolved within the same chapter
        - Generic character traits with no specific future hook
        """
    ).strip(),
}


def build_user_prompt(pass_name: str, chunk: str, context: dict, custom_entity_types: list[dict] | None = None) -> str:
    context_block = build_context_block(context)
    task = PASS_TASK_INSTRUCTIONS.get(
        pass_name, f"Execute the {pass_name} pass and return JSON only."
    )
    custom_block = ""
    if pass_name == "new_entities" and custom_entity_types:
        type_lines = "\n".join(
            f"  - {t['name']}: {t.get('description', '')}"
            for t in custom_entity_types
        )
        existing_custom = context.get("custom_entities", {})
        existing_lines = ""
        for type_name, items in existing_custom.items():
            if items:
                names = ", ".join(i["name"] for i in items)
                existing_lines += f"\n  {type_name} (already known): {names}"
        custom_block = dedent(f"""
        CUSTOM ENTITY TYPES FOR THIS NOVEL
        Extract entities of these types into the custom_entities array:
        {type_lines}

        Only extract if genuinely new and not already in STORY CONTEXT.
        Each item: {{name, type (one of the types above), description}}.
        {existing_lines}
        """).strip()
    return dedent(
        f"""
        {context_block}

        CHAPTER CHUNK
        {chunk}

        TASK
        {task}
        {custom_block}
        Return JSON only.
        """
    ).strip()


CANONICALIZATION_SCHEMA = {
    "resolutions": [
        {
            "candidate": "string (verbatim from input list)",
            "verdict": "existing|new",
            "id": "uuid string when verdict=existing, else null",
            "grammatical_anchor": (
                "verbatim substring from chapter text proving the link "
                "(apposition, possessive, restated full name); null when verdict=new"
            ),
            "reasoning": "short string",
        }
    ]
}


_CANON_RULES_BY_TYPE: dict[str, str] = {
    "character": dedent(
        """
        Rules:
        - Verdict "existing" requires BOTH an id from the roster AND a
          grammatical_anchor: a verbatim substring of the chapter text in which
          the candidate is grammatically tied to that existing character via
          apposition, unambiguous possessive, or a restated full name in
          immediate context.
        - Do NOT merge based on stylistic similarity, topical inference, plot
          guesswork, or general knowledge of the source novel. Use only the
          chapter text provided.
        - When grammatical evidence is absent, return "new". When in doubt,
          return "new". Visible duplicates are acceptable; wrong character
          merges destroy continuity.
        - "grammatical_anchor" must be an EXACT substring of the chapter text.
          If you cannot quote one verbatim, return "new".
        """
    ).strip(),
    "location": dedent(
        """
        Rules:
        - Verdict "existing" requires an id from the roster AND clear reasoning
          that the candidate refers to the same physical place.
        - A verbatim grammatical_anchor is NOT required for locations. Semantic
          evidence is sufficient: the chapter describes the same building, the
          candidate name is a common variant or sub-location of an existing entry
          (e.g. "14th floor" → "Corporate Office", "Jake's home" → "Jake's
          Apartment"), or context makes the identity clear.
        - Still set grammatical_anchor to a relevant quote if one exists, else "".
        - When a candidate is clearly a sub-location (floor, room, corridor) of an
          existing roster entry, return verdict "existing" for that parent.
        - Return "new" only for genuinely distinct, named places not in the roster.
        """
    ).strip(),
    "object": dedent(
        """
        Rules:
        - Verdict "existing" requires an id from the roster AND reasoning that the
          candidate is the same physical object.
        - A verbatim grammatical_anchor is NOT required. Description similarity,
          ownership context, and narrative continuity are sufficient evidence.
        - CRITICAL: NEVER merge two objects that belong to different owners. If the
          roster entry belongs to Jake and the candidate belongs to Sarah, return
          "new" even if the names are identical.
        - A named unique item (a specific sword, a magical artifact) referred to by
          two names in the same owner context SHOULD be merged.
        - When in doubt, return "new" — duplicate objects are less harmful than
          wrong merges.
        """
    ).strip(),
    "faction": dedent(
        """
        Rules:
        - Verdict "existing" requires an id from the roster AND clear reasoning
          that the candidate is the same organisation.
        - A verbatim grammatical_anchor is NOT required. Abbreviations, aliases,
          and common shorthand are sufficient evidence when the context makes the
          identity unambiguous.
        - Still set grammatical_anchor to a relevant quote if one exists, else "".
        - Return "new" only for organisations clearly distinct from those in the
          roster.
        """
    ).strip(),
}


_CANON_RULES_GENERIC = dedent(
    """
    Rules:
    - Verdict "existing" requires an id from the roster AND clear reasoning
      that the candidate refers to the same entity.
    - A verbatim grammatical_anchor is NOT required. Name variants,
      abbreviations, and shorthand are sufficient evidence when context makes
      the identity unambiguous.
    - Still set grammatical_anchor to a relevant quote if one exists, else "".
    - Return "new" only for entities clearly distinct from those in the roster.
      When in doubt, return "new" — duplicates are less harmful than wrong merges.
    """
).strip()


def build_canonicalization_system_prompt(entity_type: str = "character") -> str:
    # Custom (user-defined) entity types get the generic semantic rules; the
    # strict verbatim-anchor requirement only applies to characters.
    rules = _CANON_RULES_BY_TYPE.get(entity_type, _CANON_RULES_GENERIC)
    return dedent(
        f"""
        You are a strict {entity_type} canonicalizer for a novel continuity pipeline.
        For each candidate name, decide whether it refers to an EXISTING {entity_type}
        in the roster or is a NEW {entity_type}.

        {rules}

        - Return ONLY strict JSON matching the schema. No prose, no markdown.

        Output schema:
        {json.dumps(CANONICALIZATION_SCHEMA, ensure_ascii=True, indent=2)}
        """
    ).strip()


def build_canonicalization_user_prompt(
    chapter_text: str,
    candidates: list[str],
    roster: list[dict],
    entity_type: str = "character",
) -> str:
    return dedent(
        f"""
        CHAPTER TEXT
        {chapter_text}

        EXISTING {entity_type.upper()} ROSTER (JSON)
        {json.dumps(roster, ensure_ascii=True, default=str)}

        CANDIDATE NAMES TO RESOLVE (JSON)
        {json.dumps(candidates, ensure_ascii=True)}

        TASK
        For each candidate, return a resolution per the schema. Return JSON only.
        """
    ).strip()


INTRA_DEDUP_SCHEMA = {
    "groups": [
        {
            "names": ["string (verbatim from input list — only names that refer to the same entity)"],
            "reasoning": "string",
        }
    ]
}


_INTRA_DEDUP_RULES: dict[str, str] = {
    "character": dedent(
        """
        Rules:
        - Only group names when the chapter text makes it UNAMBIGUOUS they are the
          same person (e.g. "Jane" and "Jane Bennet" used interchangeably, an
          explicit apposition like "Eliza, that is Miss Bennet").
        - Do NOT group based on general knowledge of the source material.
        - When in doubt, do NOT group. Wrong character merges are catastrophic.
        """
    ).strip(),
    "location": dedent(
        """
        Rules:
        - Group name variants that clearly refer to the same place ("Jake's home"
          and "Jake's apartment", "Netherfield" and "Netherfield Park").
        - Group a sub-location with its parent when the chapter makes clear one is
          inside the other ("14th floor of the corporate office" → "Corporate
          Office", "elevator in the office building" → "Corporate Office"). Use the
          PARENT (more general) name as the canonical form.
        - Semantic similarity is sufficient evidence — you do not need a verbatim
          apposition. Use judgment based on description and context.
        - When in doubt, keep separate rather than merge across distinct buildings.
        """
    ).strip(),
    "object": dedent(
        """
        Rules:
        - NEVER merge two objects that belong to DIFFERENT owners, even if their
          names or descriptions sound identical. Two characters can each own "a
          black sedan" — those are two different cars.
        - DO group objects when the same owner refers to the same item by different
          names ("Jake's sword" and "the blade Jake carries" → same object).
        - If owner information is missing for both candidates, you may group only
          when the chapter text makes identity unambiguous (e.g. a named unique
          artifact referred to two ways).
        - When in doubt, do NOT group.
        """
    ).strip(),
    "faction": dedent(
        """
        Rules:
        - Group a faction with its common abbreviation or alias when the chapter
          clearly equates them ("the Empire" used as shorthand for "the Galactic
          Empire", "HYDRA" and "the HYDRA organisation").
        - Semantic similarity and context are sufficient — no verbatim apposition
          required.
        - When in doubt, keep separate.
        """
    ).strip(),
}


def build_intra_dedup_system_prompt(entity_type: str) -> str:
    rules = _INTRA_DEDUP_RULES.get(entity_type, dedent(
        f"""
        Rules:
        - Only group names when the chapter text makes it unambiguous they refer
          to the same {entity_type}. When in doubt, do NOT group.
        """
    ).strip())
    return dedent(
        f"""
        You are a strict entity deduplicator for a novel continuity pipeline.
        You will receive a list of {entity_type} entities extracted from a chapter.
        Identify groups of entries that clearly refer to the SAME {entity_type}
        based solely on the chapter text provided.

        {rules}

        - Do NOT include singleton groups (groups with only one entry).
        - Return ONLY strict JSON matching the schema. No prose, no markdown.

        Output schema:
        {json.dumps(INTRA_DEDUP_SCHEMA, ensure_ascii=True, indent=2)}
        """
    ).strip()


def build_intra_dedup_user_prompt(
    entity_type: str,
    entities: list,
    chapter_text: str,
) -> str:
    """
    entities: for objects, a list of dicts with at least {"name": str, "owner_name": str|None};
              for all other types, a list of name strings (backwards-compatible).
    """
    if entity_type == "object":
        enriched = []
        for e in entities:
            if isinstance(e, dict):
                entry: dict = {"name": e.get("name", "")}
                if e.get("owner_name"):
                    entry["owner_name"] = e["owner_name"]
                if e.get("distinguishing_properties"):
                    entry["distinguishing_properties"] = e["distinguishing_properties"]
                enriched.append(entry)
            else:
                enriched.append({"name": str(e)})
        entities_block = json.dumps(enriched, ensure_ascii=True)
        label = "OBJECT ENTITIES TO DEDUPLICATE (JSON — includes owner context)"
    else:
        names = [e if isinstance(e, str) else e.get("name", "") for e in entities]
        entities_block = json.dumps(names, ensure_ascii=True)
        label = f"{entity_type.upper()} NAMES TO DEDUPLICATE (JSON)"

    return dedent(
        f"""
        CHAPTER TEXT
        {chapter_text}

        {label}
        {entities_block}

        TASK
        Group any entries that clearly refer to the same {entity_type}.
        Return only groups of 2 or more. Return JSON only.
        """
    ).strip()
