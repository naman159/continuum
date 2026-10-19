import json
from types import SimpleNamespace

import pytest

from pipeline.extraction.canonicalizer import IntraExtractionDeduplicator


def _dedup(groups):
    return IntraExtractionDeduplicator(use_mock=False, completion_fn=lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'groups': groups})))]))


@pytest.mark.parametrize('kind,plural,proper,description', [
    ('character', 'characters', 'Toren Vale', 'the man sitting under the tree'),
    ('location', 'locations', 'Veyra', 'the town at the mouth of the river'),
    ('object', 'objects', 'Dawn', 'the bright blade he brought from home'),
    ('faction', 'factions', 'Ash Council', 'the group of elders who meet at midnight'),
])
def test_contextual_preferred_name_beats_long_description(kind, plural, proper, description):
    extraction = {'new_entities': {plural: [{'name': proper}, {'name': description}]},
                  'events': [{'involved_' + plural: [proper, description]}]}
    dedup = _dedup([{'names': [proper, description], 'canonical_name': proper, 'reasoning': 'Explicitly the same referent; proper name is preferred.'}])
    got = dedup.deduplicate(extraction, f'{proper}, {description}, arrived.')
    assert [r['name'] for r in got['new_entities'][plural]] == [proper]
    assert got['events'][0]['involved_' + plural] == [proper]
    assert extraction['new_entities'][plural][1]['name'] == description


def test_established_name_wins_even_when_only_its_alias_occurs():
    extraction = {'new_entities': {'characters': [{'name': 'Toren'}, {'name': 'the traveller'}]},
                  'state_deltas': [{'kind': 'status', 'character_name': 'the traveller'}],
                  'learnings': [{'character_name': 'Toren', 'fact_description': 'secret'}],
                  'scenes': [{'pov_character_name': 'the traveller', 'present_character_names': ['Toren']}],
                  'canon_facts': [{'subject_name': 'the traveller', 'subject_type': 'character'}]}
    dedup = _dedup([{'names': ['Toren', 'the traveller'], 'canonical_name': 'the traveller', 'reasoning': 'Same person.'}])
    got = dedup.deduplicate(extraction, 'Toren, the traveller.', established_rosters={
        'character': [{'id': 'one', 'name': 'Toren Vale', 'aliases': ['Toren']}]})
    assert got['new_entities']['characters'][0]['name'] == 'Toren Vale'
    assert got['state_deltas'][0]['character_name'] == 'Toren Vale'
    assert got['learnings'][0]['character_name'] == 'Toren Vale'
    assert got['scenes'][0]['pov_character_name'] == 'Toren Vale'
    assert got['canon_facts'][0]['subject_name'] == 'Toren Vale'


def test_generic_phrase_cannot_join_established_distinct_people():
    extraction = {'new_entities': {'characters': [{'name': n} for n in ['Toren', 'Mira', 'the traveller']]}}
    group = {'names': ['Toren', 'Mira', 'the traveller'], 'canonical_name': 'Toren', 'reasoning': 'Model overmerged.'}
    dedup = _dedup([group])
    assert dedup.deduplicate(extraction, 'Two travellers.', established_rosters={'character': [
        {'id': 'one', 'name': 'Toren', 'aliases': []}, {'id': 'two', 'name': 'Mira', 'aliases': []},
    ]}) == extraction
    assert dedup.warnings


def test_overlapping_generic_groups_and_shared_object_names_abstain():
    extraction = {'new_entities': {'characters': [{'name': n} for n in ['Toren', 'Mira', 'the traveller']]}}
    dedup = _dedup([{'names': [name, 'the traveller'], 'canonical_name': name, 'reasoning': 'Ambiguous shared description.'}
                    for name in ['Toren', 'Mira']])
    assert dedup.deduplicate(extraction, 'Two travellers.') == extraction
    objects = {'new_entities': {'objects': [{'name': 'sword', 'owner_name': 'Toren'},
                                          {'name': 'sword', 'owner_name': 'Mira'}, {'name': 'blade'}]}}
    dedup = _dedup([{'names': ['sword', 'blade'], 'canonical_name': 'blade', 'reasoning': 'Unsupported grouping.'}])
    assert dedup.deduplicate(objects, 'Each had a sword.') == objects


@pytest.mark.parametrize('preferred', [None, 'Invented Name'])
def test_missing_or_invented_preference_keeps_input_names(preferred):
    extraction = {'new_entities': {'characters': [{'name': 'Toren'}, {'name': 'the traveller'}]}}
    dedup = _dedup([{'names': ['Toren', 'the traveller'], 'canonical_name': preferred, 'reasoning': 'Same.'}])
    assert dedup.deduplicate(extraction, 'Toren, the traveller.') == extraction
    assert dedup.warnings
