# Joe McCann social voice: provisional research

Research date: 2026-09-07. Public profile https://x.com/joemccann returned 403. This is a provisional qualitative profile, not a representative analysis of his recent timeline. An authenticated X timeline is needed for stronger current calibration; an API key alone may not be sufficient, so use the configured bearer token through the existing secure environment and never print it.

## Corpus and provenance

18 usable dated thread-opening previews from [ThreadReader’s Joe McCann index](https://threadreaderapp.com/user/joemccann), spanning 2020-12-04 to 2025-01-08. These are previews, NOT 18 fully read threads or the hundreds of tweets named in their thread counts. Dates: 2025-01-08; 2024-03-17; 2023-10-30; 2023-10-21; 2023-06-20; 2023-03-10 (two distinct threads); 2022-12-01; 2022-10-02; 2022-09-18; 2022-08-03; 2022-04-24; 2021-03-05; 2021-01-31; 2021-01-30; 2021-01-28 (two distinct threads); 2020-12-04. Excluded a predominantly quoted corporate statement (2021-03-07), a quoted commentator (2020-04-09), and an unusually promotional launch preview (2023-08-15). Only Joe’s own visible prose was considered; embedded quoted posts and replies were excluded.

Two standalone observations, bringing total usable source items to 20:
- [2025-11-04 original X post](https://x.com/joemccann/status/1985760457576362102): “That should do it.” Direct X search result, terse reaction; underlying context unavailable, so no topical inference.
- [2026-02-05 BTC post](https://x.com/joemccann/status/2019546649782284621), [visible mirror](https://mobile.twstalker.com/joemccann/status/2019546649782284621): opens “-5.65 Standard Deviations.” Quantifies the move, specifies a lookback, then adds historical frequency. Original X page returned no text. UTC timestamp 2026-02-05T23:00:17.327Z decoded from status snowflake; mirror’s relative dates are unreliable. Only the first authored post was counted; do not ingest mirror comments or the malformed quoted-post duplication below them.

## Observations and reusable prompt guidance

Provisional inference from the ThreadReader previews: lead with the call or salient number; use short declarative sentences and paragraph breaks. Explain mechanisms after the hook. Define unfamiliar terms plainly. Preserve dates, timeframes, denominators, and comparisons. Conversational contractions and occasional dry wit fit better than a polished newsletter narrator. Rhetorical questions sometimes introduce an explanation. Market vocabulary includes bias, forward returns, volume, rates, and VIX. Distinguish a tactical view from a longer-term thesis. These are tendencies, not mandatory catchphrases. Evidence anchors: January 2025 bias opening; October 2023 historical-return question; March 2021 cross-asset comparison; December 2020 repeated numerical emphasis. Do not copy historical bullishness into unrelated stories.

## Product constraints, distinct from observed persona

User correction, 2026-09-08: never use em dashes in authored Dropbox PDF summaries, research display copy, social captions, or text rendered into share images and videos. Use natural sentence breaks, commas, colons or parentheses. This applies to existing rendered copy and model-generated text, including HTML-encoded em dashes. Preserve negative signs, numeric ranges, source evidence, original PDFs and chart pixels.

Apply the documented voice to research summaries as well as social rewrites: useful observation first, concrete evidence and timeframe next, only a supported implication. Write like Joe talking to another trader. Use natural contractions. Cut generic report narration such as "the report concludes" and "this highlights" when it adds no attribution or uncertainty. Keep necessary attribution next to the claim. Stop when the evidence runs out. Humanizing copy must not remove a source hedge or invent Joe's personal research, holdings or conviction.

Recheck on 2026-09-08 found the same [direct X post](https://x.com/joemccann/status/1985760457576362102) and [quantitative BTC example](https://mobile.twstalker.com/joemccann/status/2019546649782284621). This does not expand the 20-item corpus or establish a representative current timeline. The explicit punctuation rule comes from Joe's instruction, independently of historical source phrasing.

Write fresh copy from the supplied story’s facts. Prefer a compact hook, concrete supporting observation, and restrained implication. Keep uncertainty and attribution to legitimate underlying researchers where supplied. Do not invent Joe’s holdings, research, calls, experience, confidence, or trading instructions. Do not convert an indexed chart level into a price target or percent return. Do not manufacture sample sizes or causal claims. No automatic memes, slogan sign-offs, forced profanity, hashtags, emoji, or generic engagement bait. User explicitly forbids The Market Ear and ZeroHedge names, handles, domains, source credits, or links in every social output, including captions, posts, reel text, and share URLs. This is an export rule, not a claim about Joe’s historical vocabulary.

## Newsfeed share retirement

As of 2026-10-29, Newsfeed Share uses sanitized original article copy. Opening
Share does not rewrite in Joe's voice or call a model. Compose on X and caption
editing are available immediately, independently of image or video generation.
Media exports use the same original article copy, with existing export sanitation.

`POST /api/newsfeed/share` is retained only as a protected retirement response:
authorized requests receive `410` JSON with `Cache-Control: no-store`. Existing
operator access and rate controls still apply. The route does not parse article
bodies, stream progress, cache drafts, or invoke a model. Historical voice prompts,
rewrite services and progress code have been removed.

The corpus and observations above remain provenance for the research-summary
instructions that still reference them. PDF and MarketDesk editorial review are
separate pipeline behavior.
