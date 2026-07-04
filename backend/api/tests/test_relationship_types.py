from api.relationship_types import is_symmetric, resolve_symmetric


def test_is_symmetric_matches_known_mutual_labels():
    assert is_symmetric("spouse_of") is True
    assert is_symmetric("Sibling_Of") is True
    assert is_symmetric("mentor_of") is False
    assert is_symmetric(None) is False


def test_resolve_symmetric_prefers_stored_value_over_label():
    # Label says "friend_of" (in the static allowlist), but the extractor judged
    # this particular instance as one-sided.
    assert resolve_symmetric("friend_of", False) is False
    assert resolve_symmetric("mentor_of", True) is True


def test_resolve_symmetric_falls_back_to_label_when_unset():
    assert resolve_symmetric("spouse_of", None) is True
    assert resolve_symmetric("mentor_of", None) is False
