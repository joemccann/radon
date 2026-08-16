# Mobile Newsfeed Item Layout Spec

Target: `body[data-mobile="true"]` (viewport <= 640px), reference frame 393x852.
Surface: `DashboardNewsFeed` rendered inside `Feed / 01` on the Dashboard.
Direction: Instrument Rack. Dense, mono-dominant, hairline separation, no blog rhythm.

**Hard constraint.** `web/app/globals.css` is owned by another workflow. Every rule in this
spec lands in a new `web/components/DashboardNewsFeed.module.css`. Nothing in `globals.css`
is edited. Rules that must beat an existing global mobile rule win on specificity, not on
source order.

**Desktop must not regress.** Every layout rule in the module is prefixed with
`:global(body[data-mobile="true"])`. The only unprefixed module rules are two `display: none`
defaults for the timestamp/tag variants described in section 5 and section 3, written
`.tsCompact` and `.tagMore.tagMore`. No component reads `useViewport()`; the mobile/desktop
split is entirely CSS, so there is no hydration flash and no SSR mismatch.

**Amended after adversarial review (2026-08-15).** Six defects were found against the first
implementation. The three that land in this stylesheet are folded into the sections below and
flagged inline: the safe-area inset opened a mid-page hole (section 8), the chip hit area
resolved to 30px rather than the spec'd 32 (section 3), and `NewsfeedTagBar` kept the global
44px chip while item chips shrank to 24 (section 3). Section 4's overflow-ancestor claim was
false as written and is corrected. The quote normaliser's parity rule inverted on plural
possessives and is replaced (section 9).

---

## 0. Specificity contract

Existing global mobile rules look like `body[data-mobile="true"] .news-feed-item` and compute
to `(0,2,1)`. A bare module class computes to `(0,1,0)` and loses.

Every layout rule in the module is written as:

```css
:global(body[data-mobile="true"]) .x.x { ... }
```

`(0,3,1)`, which beats every global rule that touches these elements regardless of which
stylesheet the bundler injects first. The doubled class is the CSS Modules idiom for a
specificity bump and compiles to `.DashboardNewsFeed_x__hash.DashboardNewsFeed_x__hash`.

Where the element is rendered by a child component and cannot receive a module class
(`StarToggle`, `NewsfeedTagBar`), reach it through the module scope with a `:global()` leaf
descended from `.card.card`, e.g.
`:global(body[data-mobile="true"]) .card.card :global(.news-feed-tag-bar-chip)` at `(0,4,1)`.

The unprefixed desktop defaults take the same doubled-class bump when they have a global
competitor. `.tagMore` is `(0,1,0)` and merely **ties** `.news-feed-tag-chip { display:
inline-flex }` (globals.css:5165), so it won only on stylesheet injection order; it is written
`.tagMore.tagMore`. `.tsCompact` has no competing global rule and stays single.

Leave this comment at the top of the module:

```css
/* Mobile-only layout for the Feed / 01 news item. These rules live here, not in
   globals.css, because globals.css was under concurrent edit when this shipped.
   Consolidate them back into the `body[data-mobile="true"] .news-feed-*` block in
   globals.css in a later pass, and drop the doubled-class specificity bumps at the
   same time. See docs/mobile-newsfeed-layout.md. */
```

---

## 1. Vertical spacing scale

Four steps. Declared once on the card scope. Every gap inside the item comes from this
set. No other vertical value appears anywhere in the mobile block.

```css
:global(body[data-mobile="true"]) .card.card {
  --nf-s1: 4px;
  --nf-s2: 8px;
  --nf-s3: 12px;
  --nf-s4: 16px;
}
```

| Step | px | Purpose |
| --- | --- | --- |
| `--nf-s1` | 4 | Intra-control gap: icon to label in the link pill and the refresh control. Also the `margin-top` bump that lifts a block gap from `--nf-s2` to `--nf-s3`. |
| `--nf-s2` | 8 | Item base flex gap. Headline to summary. Tag chip gap, both axes. Footer child gap. Card title to refresh control. |
| `--nf-s3` | 12 | Block gap between the lower item blocks. Card inline padding. Card block-start padding. Header to list. |
| `--nf-s4` | 16 | Item block padding, top and bottom. Produces 32px plus a 1px rule between items. |

Applied gaps, exact:

| Boundary | Mechanism | px |
| --- | --- | --- |
| Card padding | `padding: var(--nf-s3) var(--nf-s3) 0` | 12 / 12 / 0 |
| Header to list | `.header { margin-bottom: var(--nf-s3) }` | 12 |
| Item block padding | `.item { padding: var(--nf-s4) 0 }` | 16 top, 16 bottom, **0 inline** |
| Item base block gap | `.item { gap: var(--nf-s2) }` | 8 |
| Headline to summary | base gap only | 8 |
| Summary to image | base gap + `.imageWrapper { margin-top: var(--nf-s1) }` | 12 |
| Image to tags | base gap + `.tags { margin-top: var(--nf-s1) }` | 12 |
| Tags to footer | base gap + `.footer { margin-top: var(--nf-s1) }` | 12 |
| Item to item | 16 + 1px `--border-dim` rule + 16 | 33 |

The `margin-top: var(--nf-s1)` on the three lower blocks is deliberate and also overrides
the stray `margin-top: 4px` already present on `.news-feed-tags` and `.news-feed-footer` in
globals.css, which today stacks on the flex gap and silently produces 18px.

The item's inline padding drops to **0**. The card's 12px inline padding is the only inset.
The 1px item rule then spans the full card interior, which is the correct terminal reading.

---

## 2. Type scale at 393px

Text column width: `393 - 24 (.content padding) - 2 (card border) - 24 (card padding) - 0
(item padding) = 343px`. That is 87.3% of the viewport, up from 75.1%.

| Element | font-size | line-height | weight | tracking | color | measure |
| --- | --- | --- | --- | --- | --- | --- |
| `.news-feed-headline` | 16px | 20px (1.25) | 700 | 0.01em | `var(--text-primary)` | 343px, approx 42ch, `text-wrap: balance` |
| `.news-feed-summary` | `var(--text-body)` (13px) | 18px (1.3846) | 400 | 0 (inherit) | `var(--text-secondary)` | `max-inline-size: none`, natural 343px, approx 60ch |

Line-height is expressed as a unitless ratio in CSS (`1.25`, `1.3846`) so the computed box is
exactly 20px and 18px at those sizes. Do not write the px values.

Headline drops 18 -> 16 so a two-clause Market Ear headline lands in two lines instead of
three at the wider measure. Desktop keeps 18px / 1.3.

Summary drops 14 -> 13 and 1.6 -> 1.3846. 18px leading on 13px type is the dense-terminal
setting; 22.4px on 14px was the blog setting. Desktop keeps `var(--text-prose)` / 1.6.

`white-space` changes from `pre-wrap` to **`pre-line`** on mobile. The scraper joins multiple
`.content` nodes with `\n` (`scripts/newsfeed/extract.js:49`), so hard breaks must survive, but
runs of collapsed whitespace inside a scraped paragraph must not. Paired with the newline
normalisation in section 9, no paragraph boundary costs more than one blank line.

Do not clamp or truncate the summary. The full body copy stays.

---

## 3. Tag chips

### Sizing

| Property | Value |
| --- | --- |
| `display` | `inline-flex; align-items: center` |
| `min-height` | `24px` |
| `padding` | `0 var(--nf-s2)` (0 top/bottom, 8 inline) |
| `min-width` | `auto` (explicitly cancels the global 44px floor) |
| `font-size` | `10px` |
| `line-height` | `1` |
| `letter-spacing` | `0.06em` |
| `font-family` | `var(--font-mono)` |
| `border` | `1px solid var(--border-dim)`, `border-radius: 4px` |
| Row gap / column gap | `var(--nf-s2)` = 8px both axes |
| Row height | 24px |

Vertical centring comes from `min-height` plus `align-items: center`, not from padding, so
the box height is exactly 24px and the mono baseline sits on the row centre.

### Tap target

The chip is a **secondary filter affordance**, not a primary action. It meets the WCAG 2.5.8
AA 24px floor with its own box and is expanded to a 32px hit area with a pseudo-element, so
the visual box never inflates:

```css
:global(body[data-mobile="true"]) .tagChip.tagChip { position: relative; }
:global(body[data-mobile="true"]) .tagChip.tagChip::after {
  content: "";
  position: absolute;
  inset: -5px -2px;
}
```

**`-5px`, not `-4px`.** A negative `inset` on an absolutely positioned box resolves against
the containing block's **padding box**, and `.news-feed-tag-chip` carries a 1px border, so the
pad grows from 22px rather than the 24px border box. `-4px` measured 30px live, not the 32
this section claims. `-5px` measures 32.

32px of hit height against a 32px row pitch (24 chip + 8 row gap) means adjacent rows touch
and never overlap. The global `body[data-mobile="true"] button { min-height: 44px;
min-width: 44px }` is what produced both the 44px-wide `SPX` chip and the 94px tag row; the
module cancels it for chips only, with `min-height: 24px; min-width: auto`.

### The tag bar is the same chip and must be the same size

`NewsfeedTagBar` renders inside `section.dashboard-news`, which carries `styles.card`, but its
chips are plain global classes. The first implementation shrank only the item chips, so an
active filter put a 44px `.news-feed-tag-bar-chip SPX` two rows above a 24px
`.news-feed-tag-chip SPX` with identical text, both teal-active. One chip, one size:

```css
:global(body[data-mobile="true"]) .card.card :global(.news-feed-tag-bar-chip) {
  position: relative;
  min-height: 24px;
  min-width: auto;
  padding: 0 var(--nf-s2);
  line-height: 1;
}
:global(body[data-mobile="true"]) .card.card :global(.news-feed-tag-bar-chip)::after {
  content: "";
  position: absolute;
  top: 50%;
  left: 50%;
  width: var(--touch-min);
  height: var(--touch-min);
  transform: translate(-50%, -50%);
}
```

The bar chip keeps the **full 44px** pseudo hit area rather than the item chip's 32. It is a
destructive dismiss control (tapping it removes an active filter), not a secondary filter
affordance, so it sits under the primary-control rule from section 4, not the WCAG 2.5.8 AA
floor. `.news-feed-tag-bar-clear` takes the identical treatment for the same reason — leaving
it at the global 44px box would only move the size mismatch one control to the right.

`NewsfeedTagBar.tsx` is not modified; both rules are reached through the card scope so nothing
lands in `globals.css`.

### Overflow rule: 4 visible plus a tappable `+N`

**Decision: a four-chip cap with a `+N` expander, wrapping allowed to a hard maximum of two
rows.** A horizontal scroll strip steals the horizontal gesture inside a vertically scrolling
list and hides tags off-screen with no affordance; shrinking chips below 10px mono falls under
the legible floor. A count cap needs no measurement, keeps one row in the common case, never
exceeds two, and leaves every tag one tap away.

Implementation is CSS-only for the desktop/mobile split, with one piece of local state for
the expansion:

- Render **all** chips always. Chips at index >= 4 also carry `styles.tagOverflow`.
- When `postTags.length > 4`, render one extra chip after them, `styles.tagMore`, labelled
  `+{postTags.length - 4}`, `type="button"`, `aria-expanded={expanded}`,
  `aria-label={`Show ${postTags.length - 4} more tags`}`.
- Module defaults (unprefixed, so desktop gets them): `.tagMore { display: none }`. Overflow
  chips are visible by default, so desktop is byte-identical to today.
- Mobile: `.tagOverflow { display: none }`, `.tagMore { display: inline-flex }`.
- Mobile, expanded (the `.tags` container also carries `styles.tagsExpanded`):
  `.tagsExpanded .tagOverflow { display: inline-flex }`,
  `.tagsExpanded .tagMore { display: none }`.

State: `const [expandedTags, setExpandedTags] = useState<Set<string>>(new Set())` keyed by
`post.id`, in `DashboardNewsFeed`. Tapping `+N` adds the id. Collapsing again is not offered;
expansion is one-way per session, which is the cheaper interaction.

`display: none` removes the hidden chips from the tab order and the accessibility tree, so
desktop keyboard behaviour is unchanged and mobile does not tab through hidden filters.

Measured at 393px with the operator's five tags: `SPX` 44 + `VOLATILITY` 84 + `OPTIONS` 64.2
+ `MACRO` 51 + `+1` 31.2, plus 4 x 8px gaps = 306.4px against a 343px column. One row, 24px
tall, down from two rows and 94px.

---

## 4. Footer row

One row. Three children, all normalised to the **same 24px box height**, so their centres and
their mono baselines coincide.

```
[ LINK pill ]  [ timestamp .......................... ]  [ star ]
   24px              24px, flex:1 1 auto, nowrap           24px
```

| Property | Value |
| --- | --- |
| `.news-feed-footer` | `display: flex; align-items: center; gap: var(--nf-s2); margin-top: var(--nf-s1); position: relative; z-index: 1` |
| `.news-feed-link-pill` | `flex: 0 0 auto; min-height: 24px; padding: 0 var(--nf-s2); gap: var(--nf-s1); display: inline-flex; align-items: center; position: relative` |
| `.news-feed-timestamp` | `flex: 1 1 auto; min-width: 0; min-height: 24px; display: inline-flex; align-items: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis` |
| `:global(.star-toggle)` | `flex: 0 0 auto; min-height: 24px; min-width: 24px; padding: 0; margin-left: auto; position: relative` |

`justify-content: space-between` is removed. The timestamp is the flexible child, so it
absorbs the slack and the star is pinned right by `margin-left: auto`.

Long-timestamp behaviour: the timestamp is `white-space: nowrap` and, because section 5
reduces it to a single token, it cannot exceed 66px against 236.8px of available width. If a
future locale ever overflows, it ellipsises on one line. **It can never wrap, so it can never
orphan a fragment like `PM`.**

### 44px tap targets without a 44px box

Both the link pill and the star keep a full 44x44 hit area via a centred pseudo-element,
leaving the layout box at 24px:

```css
:global(body[data-mobile="true"]) .linkPill.linkPill::after,
:global(body[data-mobile="true"]) .footer.footer :global(.star-toggle)::after {
  content: "";
  position: absolute;
  top: 50%;
  left: 50%;
  width: var(--touch-min);
  height: var(--touch-min);
  transform: translate(-50%, -50%);
}
```

This is the pattern that avoids the known failure mode: a 44px `min-height` on a control
inside a sub-44 container with `overflow: hidden`.

**Correction (adversarial review, 2026-08-15).** The original text here claimed "no ancestor
of the footer sets `overflow: hidden`". That is **false**. `.main` is `overflow: hidden`
(globals.css:1059) and `.content` is `overflow-y: auto` (globals.css:1402), and both are
ancestors of the footer. The pads are nevertheless not clipped, for a narrower reason that a
future consolidation pass must re-check rather than assume:

- The nearest ancestors — `.news-feed-item`, `.news-feed-list`, `.dashboard-news__body`,
  `.dashboard-news` — do leave `overflow` visible, so nothing clips inside the card.
- A 44px pad centred on a 24px box overhangs by 10px per side. It is absorbed entirely by the
  item's own 16px block padding and the card's 12px inline padding, so the pad never reaches
  the `.content` scroll port, let alone `.main`.
- `.content` scrolls rather than clips, so even content that did reach its edge would be
  reachable, not cut.

Any future pad large enough to escape those 12/16px inner reserves has to be measured against
`.content`, not waved through on the old claim. `z-index: 1` on the footer keeps the star's
hit area above the tag chips' hit area where the two overhang by 2px at the right edge.

Star glyph stays at 18px inside the 24px box.

Row height: 24px, down from 44px.

---

## 5. Timestamp string

Today: `formatRelative()` concatenated with `" at "` and `formatTime()`, rendered as
`26 MINUTES AGO AT 06:00 PM`, while `formatAbsolute()` is already on `title`. The clock is
printed twice and the relative phrase is printed at full length.

**Mobile prints exactly one token, derived from `post.isoTimestamp`.** No cadence copy is
hardcoded anywhere; every branch is a function of `now - timestamp`.

New export in `web/lib/newsfeedTime.ts`:

```ts
const COMPACT_DATE_OPTS: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };

/** Single-token timestamp for narrow surfaces. Relative inside a week, short date beyond. */
export function formatCompact(timestamp: string, now: number = Date.now()): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  const diff = now - date.getTime();
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "moments ago";
  if (diff < hour) return `${Math.max(1, Math.round(diff / minute))} min ago`;
  if (diff < day) return `${Math.max(1, Math.round(diff / hour))} hr ago`;
  if (diff < 7 * day) {
    const days = Math.max(1, Math.round(diff / day));
    return `${days} day${days === 1 ? "" : "s"} ago`;
  }
  return new Intl.DateTimeFormat(LOCALE, COMPACT_DATE_OPTS).format(date);
}
```

Rendered output after the existing `text-transform: uppercase`: `MOMENTS AGO`, `26 MIN AGO`,
`3 HR AGO`, `2 DAYS AGO`, `AUG 12`. Longest is `MOMENTS AGO` at 11 characters, approx 73px at
10px mono with 0.06em tracking, against 236.8px available.

A negative `diff` (clock skew, a future-stamped post) falls into the first branch and reads
`MOMENTS AGO`, matching the existing `formatRelative` behaviour.

**Desktop keeps today's string unchanged:** `relative` + `" at "` + `time`.

Both are rendered, one is hidden by CSS. No `useViewport()`, no hydration swap:

```tsx
<span className={`news-feed-timestamp ${styles.timestamp}`} title={absolute}>
  <span className={styles.tsCompact}>{compact}</span>
  <span className={styles.tsFull}>
    {relative}
    {time ? ` at ${time}` : ""}
  </span>
</span>
```

```css
.tsCompact { display: none; }                                    /* desktop default */
:global(body[data-mobile="true"]) .tsCompact.tsCompact { display: inline; }
:global(body[data-mobile="true"]) .tsFull.tsFull { display: none; }
```

`title={absolute}` stays on the wrapper for desktop hover and for assistive tech. That is now
the only place the absolute clock appears on mobile, which removes the redundancy.

---

## 6. The empty band above the first headline

Measured, section top to first headline top: **187.5px**. Nothing occupies it. It is five
additive boxes:

| Contributor | px | Disposition |
| --- | --- | --- |
| `.dashboard-section__toggle` 44px + 8px margin | 52 | **Keep.** It is a real off-card control with a legitimate 44px target. Do not edit `DashboardSurface.tsx`. |
| `.dashboard-news` `padding-top: 14px` | 14 | -> `var(--nf-s3)` = 12 |
| `.dashboard-news__heading` eyebrow 12 + gap 2 | 14 | **Remove.** `panel-eyebrow` reads `Feed / 01`, which duplicates the section toggle's `LIVE MARKET FEED 01` two rows above. Mobile only. |
| `.panel-title` | 17.5 | Keep |
| Header `flex-direction: column` + 10px gap + 44px refresh row | 54 | **Remove.** Header returns to a single row; refresh sits beside the title. |
| Header `margin-bottom: 12px` | 12 | Keep, now `var(--nf-s3)` |
| `.news-feed-item` `padding-top: 24px` | 24 | -> `var(--nf-s4)` = 16 |

Rules:

```css
:global(body[data-mobile="true"]) .card.card {
  padding: var(--nf-s3) var(--nf-s3) 0;
}
:global(body[data-mobile="true"]) .header.header {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  flex-wrap: nowrap;
  gap: var(--nf-s2);
  margin-bottom: var(--nf-s3);
}
:global(body[data-mobile="true"]) .heading.heading :global(.panel-eyebrow) {
  display: none;
}
:global(body[data-mobile="true"]) .actions.actions {
  width: auto;
  flex: 0 0 auto;
}
:global(body[data-mobile="true"]) .refresh.refresh {
  position: relative;
  min-height: 24px;
  min-width: auto;
  padding: 0;
  gap: var(--nf-s1);
}
:global(body[data-mobile="true"]) .refresh.refresh::after {
  content: "";
  position: absolute;
  top: 50%;
  left: 50%;
  width: var(--touch-min);
  height: var(--touch-min);
  transform: translate(-50%, -50%);
}
```

The refresh control keeps its 44x44 hit area via the same pseudo-element pattern, so the
30px of dead space inside a borderless 13px mono label is gone while the target is intact.

Result: section top to first headline top = `44 + 8 + 12 + 24 (header row) + 12 + 16` =
**116px**, down from 187.5. `REFRESH` ink bottom to headline ink top = `6 + 12 + 16` = **34px**,
down from 52.5.

---

## 7. Image treatment

Three nested 1px frames today: the card border, the `.news-feed-image-wrapper` border, and
the image's own inset `--img-outline`. The wrapper border and the image outline sit 1px apart
and read as one doubled frame.

**Keep exactly one frame: the image's own inset outline.** The wrapper contributes no border.
The image spans the full 343px text column, flush with the headline and summary left and
right edges. It does **not** full-bleed past the card padding; a negative-margin bleed would
collide with the card's 4px radius and its 1px `--line-grid` border.

```css
:global(body[data-mobile="true"]) .imageWrapper.imageWrapper {
  margin-top: var(--nf-s1);
  border: 0;
  border-radius: 4px;
  overflow: hidden;
  background: var(--bg-base);
  aspect-ratio: 16 / 9;
  min-height: 0;
  min-width: 0;
}
:global(body[data-mobile="true"]) .image.image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 4px;
}
```

- `aspect-ratio: 16 / 9` on the wrapper gives a fixed 343 x 192.9 slot regardless of the
  source ratio, so a tall chart cannot blow out the item and there is no layout shift while
  `next/image` loads.
- `object-fit: cover` crops to that slot. `next/image` keeps its `width={1200} height={675}`
  intrinsic hint.
- `min-height: 0; min-width: 0` cancels the global 44px button floor on the wrapper button.
- The image keeps `outline: 1px solid var(--img-outline); outline-offset: -1px` from
  globals.css. That is the only frame inside the card. `--img-outline` is already a
  theme-neutral token (`oklch(1 0 0 / 0.1)`), so both themes are correct with no change.
- `border-radius: 4px` is the panel maximum, applied on both wrapper and image so the cover
  crop and the outline agree at the corners.
- `.news-feed-image-zoom` is hover-only and therefore inert on touch. Leave it. Do not
  promote it to always-visible on mobile; the whole wrapper is the tap target.

---

## 8. Scroll container bottom clearance

Measured finding: the real scroller is the **document**, not `.content`. `.main` already
reserves `padding-bottom: calc(var(--mobile-tab-bar-height) + var(--safe-bottom))`
(globals.css:14580), which is the same expression the fixed `.mobile-tab-bar` uses for its
own height. At maximum scroll, 0px of the feed is occluded. Nothing is stranded.

What the operator captured is a mid-scroll frame where item 2 runs under the opaque bar. That
is normal scrolling, but three things make it read as a clip:

1. The card border ends flush against the tab bar at max scroll.
2. Anchored or programmatic scrolls land items under the bar.
3. `.app-shell { height: 100vh }` (globals.css:262) is never overridden on mobile while
   `.main` uses `min-height: 100dvh`. On iOS the vh/dvh delta lets the outer document scroll
   by roughly the URL bar height, dragging the reserved band partly under the fixed bar.

Do **not** duplicate the tab bar reservation inside the card; that would double-count and add
64px of dead space at the end of the feed.

**The same applies to the safe-area inset, and the first implementation got it wrong.** It put
`margin-bottom: calc(var(--safe-bottom) + var(--nf-s3))` on `.card`, which assumed the feed
card ends the mobile page. It does not: `.dashboard-surface__right` follows it inside
`.dashboard-surface__grid`, which already supplies `gap: 16px` (globals.css:1805). The inset
was therefore spent **mid-scroll**, blowing out one seam and leaving every other seam at 16:

| `--safe-bottom` | Feed -> Top Candidates seam | Every other section seam |
| --- | --- | --- |
| `0px` | 28 | 16 |
| `34px` (notched iPhone) | 62 | 16 |

A 62px hole in the middle of the scroll on an iPhone with a home indicator.

**Resolution taken: option (i) — drop the inset from the card entirely.** No replacement is
needed anywhere. `body[data-mobile="true"] .main` already carries
`padding-bottom: calc(var(--mobile-tab-bar-height) + var(--safe-bottom))` (globals.css:14580),
which reserves the bar **and** the home-indicator inset on the real scroll container. The
card's copy was pure double-counting, so this is not a deferral: **there is no globals.css
follow-up outstanding for defect B.**

Module rules:

```css
:global(body[data-mobile="true"]) .card.card {
  padding: var(--nf-s3) var(--nf-s3) 0;
  /* no margin-bottom — the card is mid-page, and .main already reserves the inset */
}
:global(body[data-mobile="true"]) .item.item {
  scroll-margin-bottom: calc(var(--mobile-tab-bar-height) + var(--safe-bottom) + var(--nf-s3));
}
```

`scroll-margin-bottom` is what guarantees any scroll that targets an item lands it fully above
the bar. It costs nothing when no scroll is targeting an item, which is exactly why it is the
right home for the reservation and a `margin` is not.

Pinned by `web/e2e/mobile-newsfeed-layout.spec.ts`, "defect B", which measures all three
section seams at `--safe-bottom: 0px` and again with the token forced to `34px`.

**Deferred to the globals.css consolidation pass** (cannot be done from a module, and is the
actual iOS bug): change `.app-shell { height: 100vh }` to `height: 100dvh`, or drop the fixed
height on mobile so `.main`'s `min-height: 100dvh` is the only anchor. Track it with the
consolidation comment.

---

## 9. Leading apostrophe artifact

It is **bad source data**, not a component or an API bug. `scripts/newsfeed/extract.js:48-49`
takes `textContent` verbatim; the store, the writer, the route and the hook all pass it
through. At 13px in the sans face a lone straight `"` reads as an apostrophe.

**Corpus census, Turso `posts`, 2026-08-15** (read-only `SELECT`; supersedes the 454-post JSON
fallback figure the first draft quoted, which is stale and does not hold):

| Leading glyph | Code point | Bodies | Partnered later in the body |
| --- | --- | --- | --- |
| `"` | U+0022 | 147 | 146 |
| `“` | U+201C | 7 | 7 (all close with U+201D) |
| `‘` | U+2018 | 1 | 1 |
| `'` | U+0027 | 1 | 0 |

Out of 4,782 bodies with content, **exactly one** is an unpartnered leading double quote.

**Sanitise at the render boundary, in the shared normaliser**, `web/lib/newsfeedText.ts`.

**Parity counting was tried and is wrong.** The first implementation counted quote glyphs and
stripped the opener on an odd count, excluding only *intra-word* apostrophes. A word-final
apostrophe flips the decision, and those are everywhere in market commentary:

| Input | Parity result |
| --- | --- |
| `"The traders' book is long gamma."` (balanced) | opener stripped, closer left dangling |
| `"The traders' book is long gamma.` (unbalanced) | artifact survives |
| `'Tis the season for gamma` | legitimate elision eaten |

The balanced case came out **worse than before the fix**. The rule is therefore conservative
by construction rather than clever:

- Strip a leading glyph only when it is a **double-quote-class** character (`"`, `“`, `”`)
  **and** no other double-quote-class character appears anywhere in the rest of the string.
- **Never** touch a single-quote or apostrophe glyph, in any position. Possessives and
  `'Tis`-style elisions are untouchable, not merely excluded from a count.

Measured against the census above, the rule strips one body of 4,782 and leaves the other 153
leading-double bodies intact.

Wire it at the one place every consumer already funnels through,
`web/lib/useNewsfeedPosts.ts:119`:

```ts
content: normalisePostContent(post.content || ""),
```

That single call covers the dashboard rail, `NewsfeedLightbox` (which receives the same
`NormalisedPost`), and any future consumer of the hook. Profile bookmarks persist only
`title`, `source`, `timestamp` and `image`, so they need nothing.

Rules:

- Only body copy is touched. `title` is left alone; a quoted headline is legitimate.
- A balanced pull quote is preserved. Only an unpartnered leading **double** quote is dropped.
- Apostrophes and single quotes (`SEC's`, `doesn't`, `the traders'`, `'Tis`) are never
  inspected and never modified.
- Written with a plain replace chain and an array membership test — no lookbehind, no
  `String.prototype.at`, so older Safari is safe.
- Applying it at the hook means desktop gets it too. That is correct: an unpartnered scrape
  artifact is bad data on every surface, not just the mobile rail.

---

## 10. Module surface and component wiring

`web/components/DashboardNewsFeed.module.css` exports these classes. Each is applied
**alongside** the existing global class, never in place of it:

| Module class | Applied to |
| --- | --- |
| `card` | `section.dashboard-news.snapshot-card` |
| `header` | `header.dashboard-news__header` |
| `heading` | `div.dashboard-news__heading` |
| `actions` | `div.news-feed-actions` |
| `refresh` | `button.news-feed-refresh--rail` |
| `list` | `ul.news-feed-list` |
| `item` | `li.news-feed-item` |
| `headline` | `h3.news-feed-headline` |
| `summary` | `p.news-feed-summary` |
| `imageWrapper` | `button.news-feed-image-wrapper--button` |
| `image` | `Image.news-feed-image` |
| `tags` | `div.news-feed-tags` |
| `tagsExpanded` | `div.news-feed-tags`, conditional |
| `tagChip` | every `button.news-feed-tag-chip` |
| `tagOverflow` | chips at index >= 4 |
| `tagMore` | the `+N` chip |
| `footer` | `div.news-feed-footer` |
| `linkPill` | `a.news-feed-link-pill` |
| `timestamp` | `span.news-feed-timestamp` |
| `tsCompact` / `tsFull` | the two timestamp spans |

TSX changes in `DashboardNewsFeed.tsx`, and nothing else:

1. `import styles from "./DashboardNewsFeed.module.css";`
2. `import { formatAbsolute, formatCompact, formatRelative, formatTime } from "../lib/newsfeedTime";`
3. Append each module class to the existing `className` strings per the table.
4. Add `const compact = formatCompact(post.isoTimestamp);` beside the existing three.
5. Split the timestamp into the two spans from section 5.
6. Add the `expandedTags` state, the `tagOverflow` class on chips at index >= 4, and the
   `+N` chip from section 3.

`StarToggle.tsx`, `NewsfeedTagBar.tsx`, `NewsfeedLightbox.tsx` and `DashboardSurface.tsx` are
not modified. The tag bar's chips are restyled from this module through the card scope
(section 3), not by editing the component.

The module also reaches two child-owned globals that take no module class:

| Global selector | Reached as | Section |
| --- | --- | --- |
| `.star-toggle` | `.footer.footer :global(.star-toggle)` | 4 |
| `.news-feed-tag-bar-chip`, `.news-feed-tag-bar-clear` | `.card.card :global(...)` | 3 |

### Tokens used

`--bg-base`, `--border-dim`, `--line-grid`, `--text-primary`, `--text-secondary`,
`--text-muted`, `--text-body`, `--img-outline`, `--font-mono`, `--touch-min`,
`--mobile-tab-bar-height`, `--safe-bottom`, `--nf-s1..--nf-s4`.

No raw hex. No `rgba()` literals. Any alpha uses
`color-mix(in srgb, var(--token) X%, transparent)`. Every radius is 4px or 0. Both themes
resolve through the same tokens, so the light screenshot and the dark default are correct
with one rule set.

---

## 11. Expected measurements after the change (393x852)

| Measure | Before | After | Delta |
| --- | --- | --- | --- |
| Section top to first headline top | 187.5 | 116 | -71.5 |
| REFRESH ink to headline ink | 52.5 | 34 | -18.5 |
| Text column width | 295 (75.1%) | 343 (87.3%) | +48 |
| Headline | 18 / 23.4 | 16 / 20 | tighter |
| Summary line box | 22.4 | 18 | -4.4 per line |
| Tag row height (5 tags) | 94 (2 rows) | 24 (1 row) | -70 |
| Tag chip min-width | 44 | intrinsic | orphan removed |
| Footer row height | 44 | 24 | -20 |
| Timestamp line boxes | 2 | 1 | no orphaned `PM` |
| Item height, operator's post 1 | 530.8 | approx 409 | -122 (-23%) |
| Frames around the image | 3 | 1 | double frame removed |
| Star / link / refresh hit area | 44 / 22 / 44 | 44 / 44 / 44 | pill gains a target |
| Tag chip hit height | 44 | 32 | `inset: -5px`, not `-4px` (30) |
| Tag-bar chip box / hit area | 44 / 44 | 24 / 44 | one chip size on screen |
| Feed -> Top Candidates seam, `--safe-bottom: 34px` | 62 | 16 | mid-page hole closed |

---

## 12. Verification gate

1. **Unit, red first.** `web/tests/newsfeed-text-normaliser.test.ts`:
   - `'"Buyback bid is coming back'` -> leading quote stripped.
   - `'"Fully quoted line."'` (balanced) -> unchanged.
   - `"The SEC's data shows"` -> unchanged, apostrophe survives.
   - `'"He said "yes" and left'` -> **unchanged**; a later double quote means keep. (The
     original spec asserted the opposite here. Under parity counting the opener was stripped;
     under the conservative rule it is not, and that is the intended behaviour.)
   - `"The traders' book is long gamma."` (word-final apostrophe) -> unchanged.
   - `"'Tis the season for gamma"` -> unchanged.
   - `"a\n\n\n\nb"` -> `"a\n\nb"`.
2. **Unit.** `web/tests/newsfeedTime.test.ts` extended for `formatCompact` at 30s, 26min,
   3h, 2d, 9d, invalid input, and a future timestamp. Use window-relative dates
   (`Date.now() - N`), never hardcoded calendar dates.
3. **Full suite.**
   `cd web && NODE_ENV=test ASSISTANT_MOCK=1 npx vitest run --config ../vitest.config.ts web/tests`
4. **Typecheck / lint.** `npx tsc --noEmit`, `npx eslint app components lib middleware.ts`.
5. **E2E, mobile project.** `PLAYWRIGHT_PORT=3033 npx playwright test --project=mobile`.
   The spec is `web/e2e/mobile-newsfeed-layout.spec.ts` (11 tests, all at 393x852 against a
   stubbed feed so the numbers are deterministic). It pins:
   - `.news-feed-tags` `getBoundingClientRect().height <= 32` with 6 tags, plus a `+2`.
   - `.news-feed-timestamp` one client rect, `white-space: nowrap`, one variant rendered.
   - `.news-feed-footer` children share a centre y within 1px; row `<= 28`.
   - `.news-feed-item` `padding-left === "0px"`, `padding-top === "16px"`.
   - no element under `.dashboard-news` extends past `documentElement.clientWidth`.
   - the last item `scrollIntoView({block:"end"})` clears `.mobile-tab-bar`.
   - effective hit areas: refresh / link pill / star `>= 44`, tag chip `>= 32`.
   - **defect B** — all three dashboard section seams equal within 1px, measured at
     `--safe-bottom: 0px` and again with the token forced to `34px`.
   - **defect E** — `.news-feed-tag-bar-chip` and `.news-feed-tag-chip` box heights within
     1px, bar chip hit area `>= 44` in both axes.
   - **defect F** — after activating `+N`, `document.activeElement` is a visible `BUTTON`
     inside the same `.news-feed-tags`, and no `[aria-expanded]` element is `display: none`.

   Note on the hit-area assertions: they measure the **union of the layout box and its
   `::after` pad**, not the element's box height. A 24px control with a centred 44px pad is a
   44px target; asserting `boundingClientRect().height >= 44` would be asserting the wrong
   invariant. `web/e2e/mobile-shell.spec.ts` "contained Refresh control" was amended to the
   same measurement for the same reason, keeping its left/right containment assertions
   unchanged.
6. **Desktop no-regression.** Same assertions inverted on the default desktop project: tag
   row shows all chips, timestamp reads `... at ...`, `.news-feed-item` padding stays 24px.
7. **Live capture.** Screenshot 393x852 in both `data-theme="light"` and `data-theme="dark"`,
   dev server on **port 3033 only**. Never touch 3000, 8321 or 8765.

---

## 13. Open follow-ups

**The dead `.list.list { padding: 0 }` rule stays for now.** `.news-feed-list` already sets
`padding: 0` in globals.css:4479, so the module rule changes nothing. It cannot simply be
deleted: `.list` is the only definition of that class in the module, and `DashboardNewsFeed.tsx`
renders `className={... styles.list}`. Removing the rule makes `styles.list` `undefined`, which
renders the literal string `undefined` into the `ul`'s class list and breaks the module-class
assertion in `web/tests/dashboard-newsfeed-mobile-item.test.tsx`. Retiring it is a coordinated
three-file change (module + component + test), not a stylesheet edit, and belongs with the
globals.css consolidation pass.

**`.tagsExpanded .tagMore { display: none }` is deliberately kept** even though the `+N` button
is now unmounted on expand rather than hidden (defect F). It is inert while the component
unmounts the control, and it is the backstop that stops a partially-reverted component from
shipping a visibly stale `+N` with `aria-expanded="true"`.

**Still deferred to the globals.css consolidation pass** (unchanged by this review): change
`.app-shell { height: 100vh }` (globals.css:262) to `100dvh`, or drop the fixed height on
mobile so `.main`'s `min-height: 100dvh` is the only anchor. See section 8. Defect B itself
needs **no** globals.css change.
