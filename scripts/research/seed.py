"""Import an operator-approved private calibration batch into the durable outbox."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from research.state import work_key


def seed_reviewed(directory, state, publisher):
    directory = Path(directory).resolve(strict=True)
    batch = json.loads((directory / 'review-batch.json').read_text())
    charts = json.loads((directory / 'chart-manifest.json').read_text())['charts']
    approved = set(batch.get('selection_calibration', {}).get('accepted_ids', []))
    candidates = batch['candidates']
    if not approved or {c['review_id'] for c in candidates} != approved:
        raise ValueError('Every seed item must have explicit operator acceptance')
    metadata = json.loads((directory.parent / 'batch-manifest.json').read_text())['documents']
    documents = {d['local_path']: d for d in metadata}
    grouped = {}
    for candidate in candidates:
        source = candidate['source']
        document = documents[source['local_path']]
        pdf = Path(document['local_path']).resolve(strict=True)
        if directory.parent not in pdf.parents or hashlib.sha256(pdf.read_bytes()).hexdigest() != source['source_sha256']:
            raise ValueError('Seed PDF path or source hash mismatch')
        figures = []
        for figure in charts:
            if figure['review_id'] != candidate['review_id']:
                continue
            path = Path(figure['image_path']).resolve(strict=True)
            if directory not in path.parents or figure['source_sha256'] != source['source_sha256'] or hashlib.sha256(path.read_bytes()).hexdigest() != figure.get('image_sha256'):
                raise ValueError('Seed figure path or source hash mismatch')
            figures.append({'url': publisher.store_asset(path), 'page': figure['page_number'], 'caption': figure['caption']})
        post = {'id': publisher.stable_post_id(document['id'], 'calibration:' + candidate['review_id']),
                'title': candidate['title'], 'content': candidate['draft'], 'tags': candidate['tags'],
                'timestamp': datetime.now(timezone.utc).isoformat(), 'images': [f['url'] for f in figures],
                'source': {'kind': 'dropbox', 'publisher': {'1':'Morgan Stanley','11':'JPMorgan','13':'Deutsche Bank','15':'Deutsche Bank','18':'Goldman Sachs'}.get(str(candidate['document_index']), candidate.get('source_type', 'Research')),
                           'url': publisher.store_asset(pdf), 'documentDate': candidate.get('source_date', candidate.get('source_article_date')),
                           'folderDate': source['folder_date'], 'pages': candidate['source_pages'], 'figures': figures,
                           'fileId': document['id'], 'revision': document['rev'], 'contentHash': document['content_hash']}}
        grouped.setdefault(work_key(document), (document, []))[1].append(post)
    seeded = 0
    for key, (document, posts) in grouped.items():
        # Reconstruct the exact date scope from verified Dropbox path, not local file names.
        root = '/joe mccann/current/'
        relative = document['path_lower'][len(root):]
        scope = '/'.join(relative.split('/')[:3])
        state.ingest_page(scope, {'cursor': 'seed-relist-required', 'entries': [document]}, document['folder_date'])
        state.reset_cursor(scope)
        if state.claim(key):
            state.complete(key, {'status': 'operator_approved'}, publications=posts)
            seeded += len(posts)
    return seeded
