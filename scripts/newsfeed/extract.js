/*
  buildExtractionExpression returns the IIFE evaluated by chrome-cdp inside the
  themarketear.com tab. Plan:
    1. Target every <article class="post"> in the feed.
    2. For each article, parse the <script type="application/ld+json"> child.
       The schema holds headline, publish timestamp, canonical URL, and image list.
    3. Fall back to visible DOM nodes (.title, .body .content, <time>) when schema fields are missing.
    4. Per-article try/catch around schema parsing — one malformed article must not break the cycle.
    5. Outer try/catch — DOM-side failures map onto { ok: false, message }.
*/
export function buildExtractionExpression() {
  return `(() => {
    const base = 'https://themarketear.com';
    const toAbsolute = (url) => {
      if (!url) return null;
      try {
        return new URL(url, base).toString();
      } catch {
        return null;
      }
    };

    // JSON-LD parses preserve HTML entities (&#39;, &amp;, &quot;…); textContent
    // does not. Round-trip through a <textarea>.innerHTML to decode safely
    // without executing any markup the headline might contain.
    const decodeEntities = (s) => {
      if (typeof s !== 'string' || !s) return s;
      const el = document.createElement('textarea');
      el.innerHTML = s;
      return el.value;
    };

    const build = () => {
      const articles = Array.from(document.querySelectorAll('article.post'));
      const items = articles.map((article) => {
        const ldNode = article.querySelector('script[type="application/ld+json"]');
        let schema = null;
        if (ldNode && ldNode.textContent) {
          try {
            schema = JSON.parse(ldNode.textContent.trim());
          } catch (err) {
            schema = null;
          }
        }

        const id = (article.id || schema?.mainEntityOfPage?.['@id'] || schema?.url || '').split('/').filter(Boolean).pop() || '';
        const title = decodeEntities((schema?.headline || article.querySelector('.title')?.textContent || '').trim());
        const contentNodes = Array.from(article.querySelectorAll('.body .content'));
        const contentText = contentNodes.map((node) => (node.textContent || '').trim()).filter(Boolean).join('\\n');
        const timestamp = schema?.datePublished || schema?.dateModified || article.querySelector('time')?.getAttribute('datetime') || '';

        const imageCandidates = [];
        if (schema?.image) {
          const schemaImages = Array.isArray(schema.image) ? schema.image : [schema.image];
          for (const img of schemaImages) {
            if (typeof img === 'string') imageCandidates.push(img);
            else if (img && typeof img.url === 'string') imageCandidates.push(img.url);
          }
        }
        article.querySelectorAll('img').forEach((img) => {
          const src = img.getAttribute('src') || img.getAttribute('data-src');
          if (src) imageCandidates.push(src);
        });
        const images = Array.from(new Set(imageCandidates.map(toAbsolute).filter(Boolean)));

        return { id, title, content: contentText || decodeEntities(schema?.description || ''), timestamp, images };
      }).filter((entry) => entry.id && entry.title && entry.timestamp);

      return items;
    };

    try {
      const items = build();
      return JSON.stringify({ ok: true, items });
    } catch (error) {
      return JSON.stringify({ ok: false, message: error?.message || String(error) });
    }
  })()`;
}

export function parsePayload(raw) {
  if (raw === null || raw === undefined || raw === "") {
    return { ok: false, reason: "empty payload", source: "shape" };
  }
  if (typeof raw !== "string") {
    return { ok: false, reason: "non-string payload", source: "shape" };
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    return { ok: false, reason: `invalid JSON: ${err.message}`, source: "parse" };
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, reason: "non-object payload", source: "shape" };
  }

  if (parsed.ok === false) {
    return {
      ok: false,
      reason: parsed.message || parsed.reason || "DOM extraction error",
      source: "dom",
    };
  }

  if (parsed.ok !== true) {
    return { ok: false, reason: "missing ok flag", source: "shape" };
  }

  const items = Array.isArray(parsed.items) ? parsed.items : [];
  return { ok: true, items };
}
