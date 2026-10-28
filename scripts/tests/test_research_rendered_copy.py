"""All visible prose participates in admission; metadata is not rendered copy."""
import copy
import pytest
from research import rendered_copy


def test_all_captions_are_included_in_order_without_mutation():
    post = {'content': 'Macro body', 'source': {'summary': 'Unrendered Meta Buy', 'figures': [
        {'caption': 'First'}, {}, {'caption': ''}, {'caption': 'Last'}]}}
    before = copy.deepcopy(post)
    assert rendered_copy.content(post) == 'Macro body\n\nFirst\n\n\n\n\n\nLast'
    assert post == before


@pytest.mark.parametrize('post', [{'content': 'Body'}, {'content': 'Body', 'source': {}},
                                  {'content': 'Body', 'source': {'figures': []}}])
def test_no_figures_preserves_body(post):
    assert rendered_copy.content(post) == 'Body'


@pytest.mark.parametrize('post', [
    {'content': None}, {'content': 'Body', 'source': None},
    {'content': 'Body', 'source': {'figures': {}}},
    {'content': 'Body', 'source': {'figures': [None]}},
    {'content': 'Body', 'source': {'figures': [{'caption': None}]}},
])
def test_malformed_rendered_fields_cannot_be_silently_omitted(post):
    with pytest.raises(ValueError):
        rendered_copy.content(post)
