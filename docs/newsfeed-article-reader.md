# News article reader

The article reader is a centered, viewport-bounded dialog. Its unlabeled toolbar always exposes Close and available previous/next articles; the article title names the dialog. Desktop places an image gallery beside independently scrollable copy. At 900px and below, images and copy share one scrolling document beneath the toolbar. Safe-area padding protects phone controls.

The gallery contains all source figures and post images, deduplicated by URL. Previous/next image buttons, thumbnails, left/right keys, Home/End and horizontal swipes change only the image. They never advance the article. Image controls remain focusable at gallery boundaries with `aria-disabled`; selection refuses out-of-range indices. Article navigation retains its separate toolbar controls and outside-gallery arrow shortcuts. Inputs retain their editing shortcuts.

Enlarge gives the gallery the available dialog area. Zoom exposes a scrollable large image; Fit returns to the contained image. Escape returns to the article first, then dismisses. Closing restores focus to the opener through the existing shared dialog hook. There is no autoplay. Image failures retain the article and an original-image link.

Source captions preserve the supplied publisher, page and caption, or the per-image provider for Market Ear. Unknown attribution is never manufactured. Source PDF/original, Save, Explain and Share retain their existing handlers; Explain and Share use the selected image. Article text, generated-chart data, publication and permissions are unchanged.

## Design decisions

- Follow the selected Clear web system: existing surface/text/focus tokens and Inter, no new palette or UI dependency.
- Body copy: 16px with 1.7 line height; headings: 22–28px. Metadata: at least 12px. Main control targets: at least 44px.
- Definite frame height and `minmax(0, 1fr)` tracks bound both media and prose; portrait images use `object-fit: contain`.
- Separate article controls from image controls, with image counts and selected thumbnails. No ambiguous floating arrows or swipe-only navigation.
- One relative timestamp, with the absolute date on its title, avoids repeated timestamps.
- The requested ui-ux-pro-max SKILL.md was applied. Its search.py and data files are absent from this installation; recommendations were synthesized from the supplied skill and the existing Clear design system.

References: [W3C modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/), [W3C carousel pattern](https://www.w3.org/WAI/ARIA/apg/patterns/carousel/), `web/DESIGN_MEMORY.md`.

## Verification

Component coverage: `web/tests/newsfeed-gallery.test.tsx`, `web/tests/newsfeed-lightbox.test.tsx`. Browser coverage: `web/e2e/newsfeed-research-media.spec.ts` at 375, 768, 1024 and 1440px, both themes. Browser fixtures are illustrative and not investment research. Screenshots remain private. Cloud verification: full Vitest 11,542 passed / 21 skipped before the final accessible-name correction; final focused suite 104 passed, 98.88% line coverage across the reader/gallery. Previous/Next article accessible names match their visible labels for voice control.

## Visual review

The supplied desktop capture is the before evidence: the lower gallery and article controls leave the viewport. Read-only source retrieval reproduced the same article and portrait image in an isolated browser. Synthetic browser fixtures additionally exercise landscape images and long copy without live account or trading requests. Relevant no-slop review scores (0 absent, 3 severe): clipped controls 3 → 0; inaccessible image navigation 2 → 0; tiny reading text 2 → 0; repeated timestamps 2 → 0. Full-size source examination is provided by Enlarge/Zoom, rather than assuming a portrait chart is readable at thumbnail scale. Private 2x before/after captures use the exact JP Morgan article and its four original images at 393px and 1440px, in both themes. The responsive gallery suite also checks 375, 768, 1024 and 1440px widths, a 600px-tall desktop, focus wrapping and restoring the top of the next article. Long source captions remain keyboard-scrollable within four lines; Enlarge and Zoom preserve access to source detail.
