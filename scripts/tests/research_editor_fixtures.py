"""Explicit offline editorial responses for unrelated research orchestration tests."""
from research import humanizer


def clean_editor_reply(instruction):
    if instruction.startswith(humanizer.RUBRIC_INSTRUCTION):
        return {'findings': [], 'qualitative': []}
    return None


def certify(post):
    """Run real scoring/admission with a synthetic clean contextual review."""
    result = humanizer.review_post(post, ask=clean_editor_reply,
                                   verify=lambda _: False, record=lambda _: None)
    assert result is not None, 'Fixture copy must satisfy the real local writing gate'
    return result
