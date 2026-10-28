"""Rendered research body and captions for the shared publication scope gate."""


def content(post: dict) -> str:
    """Include every visible caption without mutating the certified post."""
    body = post['content']
    if not isinstance(body, str):
        raise ValueError('Research content must be a string')
    source = post.get('source', {})
    if not isinstance(source, dict):
        raise ValueError('Research source must be an object')
    figures = source.get('figures', [])
    if not isinstance(figures, list):
        raise ValueError('Research figures must be an array')
    parts = [body]
    for figure in figures:
        if not isinstance(figure, dict) or not isinstance(figure.get('caption', ''), str):
            raise ValueError('Research figure caption must be a string')
        parts.append(figure.get('caption', ''))
    return '\n\n'.join(parts)
