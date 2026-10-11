# Desktop credentials and preferences review

Scope: Profile Preferences and Credentials; selected Clear theme. Persistence, server-side credential verification, masking, reset semantics and typed widening confirmation remain the existing implementation. No local suites were executed.

## Layout and optical alignment

| Before | After |
| --- | --- |
| `PreferencesSection.tsx`: label was a sibling of the copy assigned to an undeclared `head` grid area. | Label and description share `settings-preference__copy`, in reading order. |
| `clear.css`: copy was a nested grid whose children inherited unrelated named areas, creating implicit columns and narrow text. | Copy uses a flex column; all outer rows have explicit `copy`, `control`, `actions` placement. |
| Desktop right column varied between 260–340px, with center-aligned row content and empty error track. | Right column is 240–300px; content starts at the top with 12px row / 32px column spacing; portal errors need no track. |
| Desktop layout stayed in two columns until 640px. | At 900px and below, preferences and credential field labels/controls stack without compressing descriptions. |
| Credential field label column had a fixed 180px minimum. | Label column can shrink within a bounded desktop grid; tablet collapses it. |
| Credential service titles were spans; verification badges floated with no configuration overview. | `CredentialsPanel.tsx`: semantic `h3`, existing badges, then a right-aligned count of stored/environment/process-configured fields. No claim of provider health. |

## Typography and state clarity

| Before | After |
| --- | --- |
| Descriptions were 13px; narrow grid tracks wrapped to a few words per line. | 14px, line-height 1.5, `max-width:65ch`, `text-wrap:pretty`. |
| Numeric inputs used proportional digits and physical left alignment. | `font-variant-numeric:tabular-nums` and `text-align:start`. |
| Credential status used monospace and could overflow a long process/source description. | Existing Inter UI font, line-height 1.5, wrapping for long status strings. |
| Draft preferences relied only on enabled Save. | Row shows `Unsaved`; in-flight save reads `Saving...`. |
| Credential service footer contained only Verify and save. | Footer explains stored values stay hidden; switches to Unsaved changes for a draft. |

## Hit areas and disclosure

| Before | After |
| --- | --- |
| Disclosure summaries were 28–32px tall; native marker presentation varied. | At least 40px desktop / 44px mobile or coarse pointer; explicit chevron rotates with native open state. |
| Preference buttons had no bounded width. | At least 64px wide / 40px tall, 44px on mobile/coarse pointer. |
| Disclosure keyboard focus relied on browser defaults. | Token-based 2px visible outline, 3px offset and existing small radius. |

## Interaction and motion

| Before | After |
| --- | --- |
| Settings buttons lacked local tactile feedback. | Enabled buttons press to `scale(0.96)`; only transform/background-color/border-color transition. |
| Disclosure state did not have a deliberate motion treatment. | 150ms transform transition; reduced-motion preference removes transitions and press transforms. |

## Surfaces and constraints

Clear matte surfaces, existing radii, hairline rules and theme tokens are retained. No new fonts, dependencies, shadows, gradients, initial entrance animations or `will-change` are introduced. The existing numeric bool controls, notices and vendor statuses retain their meaning. All screenshots and request assertions use synthetic API fixtures; no live credentials are read or submitted.

## Verification

Independent static review approved the flex copy hierarchy and portal-error behavior. GitHub runs `profile-settings-layout.spec.ts` in its curated Playwright job and uploads light/dark desktop/tablet/mobile screenshot artifacts. Exact-head results and rendered screenshot review will be recorded on the pull request.

Baseline red: GitHub run 38112585569 at 67799898 failed all six settings geometry cases (77 other shard cases passed). Desktop and mobile screenshots reproduce the supplied malformed layout; long descriptions measured only 66px in stacked viewport cases. Desktop dark baseline screenshot was visually inspected. This confirms the regression exercises the real failure rather than a structural source assertion.
