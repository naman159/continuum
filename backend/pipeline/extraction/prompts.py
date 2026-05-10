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
        "locations": [{"name": "string", "description": "string"}],
        "factions": [{"name": "string", "description": "string"}],
        "objects": [
            {
                "name": "string",
                "description": "string",
                "significance": "string",
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
}


def build_context_block(context: dict) -> str:
    return dedent(
        f"""
        STORY CONTEXT (summarized JSON)
        {json.dumps(context, ensure_ascii=True, default=str)}
        """
    ).strip()


def build_system_prompt(pass_name: str) -> str:
    schema = PASS_SCHEMAS[pass_name]
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


def build_user_prompt(pass_name: str, chunk: str, context: dict) -> str:
    context_block = build_context_block(context)
    return dedent(
        f"""
        {context_block}

        CHAPTER CHUNK
        {chunk}

        TASK
        Execute the {pass_name} pass and return JSON only.
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


def build_canonicalization_system_prompt() -> str:
    return dedent(
        f"""
        You are a strict character canonicalizer for a novel continuity pipeline.
        For each candidate name, decide whether it refers to an EXISTING character
        in the roster or is a NEW character.

        Rules:
        - Verdict "existing" requires BOTH an id from the roster AND a
          grammatical_anchor: a verbatim substring of the chapter text in which
          the candidate is grammatically tied to that existing character via
          apposition ("Mr. Darcy, the master of Pemberley"), unambiguous
          possessive ("Elizabeth's father"), or a restated full name in
          immediate context.
        - Do NOT merge based on stylistic similarity, topical inference, plot
          guesswork, or general knowledge of the source novel. Use only the
          chapter text provided.
        - When grammatical evidence is absent, return "new". When in doubt,
          return "new". Visible duplicates are acceptable; wrong merges are not.
        - "grammatical_anchor" must be an EXACT substring of the chapter text.
          If you cannot quote one verbatim, return "new".
        - Return ONLY strict JSON matching the schema. No prose, no markdown.

        Output schema:
        {json.dumps(CANONICALIZATION_SCHEMA, ensure_ascii=True, indent=2)}
        """
    ).strip()


def build_canonicalization_user_prompt(
    chapter_text: str,
    candidates: list[str],
    roster: list[dict],
) -> str:
    return dedent(
        f"""
        CHAPTER TEXT
        {chapter_text}

        EXISTING CHARACTER ROSTER (JSON)
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


def build_intra_dedup_system_prompt(entity_type: str) -> str:
    return dedent(
        f"""
        You are a strict entity deduplicator for a novel continuity pipeline.
        You will receive a list of {entity_type} names extracted from a chapter.
        Identify groups of names that clearly refer to the SAME {entity_type}
        based solely on the chapter text provided.

        Rules:
        - Only group names when the chapter text makes it unambiguous they are
          the same entity (e.g. "Jane" and "Jane Bennet" used interchangeably,
          "Netherfield" as a clear shorthand for "Netherfield Park").
        - Do NOT group based on general knowledge of the source material.
          Use only the chapter text.
        - Do NOT include singleton groups (groups with only one name).
        - When in doubt, do NOT group. Wrong merges are worse than duplicates.
        - Return ONLY strict JSON matching the schema. No prose, no markdown.

        Output schema:
        {json.dumps(INTRA_DEDUP_SCHEMA, ensure_ascii=True, indent=2)}
        """
    ).strip()


def build_intra_dedup_user_prompt(
    entity_type: str,
    names: list[str],
    chapter_text: str,
) -> str:
    return dedent(
        f"""
        CHAPTER TEXT
        {chapter_text}

        {entity_type.upper()} NAMES TO DEDUPLICATE (JSON)
        {json.dumps(names, ensure_ascii=True)}

        TASK
        Group any names that clearly refer to the same {entity_type}.
        Return only groups of 2 or more. Return JSON only.
        """
    ).strip()
