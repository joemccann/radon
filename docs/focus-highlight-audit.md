# Form focus highlight audit

Date: 2026-09-10. Baseline: `a4d4f808d1a9f1bd07d565d1a9474d64d713383c`.

## Scope and evidence

This audit follows the reported Modify Order currency-field screenshot. It examines focus indicator ownership across active web form controls, shared styles, component CSS modules, inline styles, and overflow containers. It preserves the creator-selected Clear identity documented in `web/DESIGN_MEMORY.md` and the existing financial behavior.

The baseline evidence is static source inspection plus the supplied screenshot. Scores describe this focused control-style audit, not full-app accessibility compliance, measured performance, or completed browser verification. Source line numbers below refer to the baseline. No local test suites were run.

## Assessment

Anti-pattern verdict: no redesign is warranted. The reported nested highlight is a style ownership defect, not a reason to change Clear typography, colors, or the trading dialog workflow.

| Dimension | Score / 4 | Scoped finding |
| --- | --- | --- |
| Accessibility | 2 | Composite inputs receive an inner ring; instrument search suppresses the standard ring. Existing focus borders remain visible. |
| Performance | 3 | Focus styles change paint properties; no focus-driven layout loop was found. Runtime profiling was outside scope. |
| Responsive design | 3 | Shared wrappers already have flexible sizing, but ring clipping and geometry need browser verification at narrow widths. |
| Theming | 3 | Focus colors use semantic tokens; global input radius and ring rules disagree with composite wrappers. |
| Anti-patterns | 3 | Existing controls follow Clear; inconsistent ring ownership creates an unintended nested-control appearance. |
| **Total** | **14 / 20** | **Good; correct the shared ownership rules.** |

Two P2 findings, no confirmed P0 or P1 findings. No WCAG violation is asserted from the screenshot or source alone.

## Findings

### P2: the generic focus ring follows the inner editable box

**Location:** `web/app/clear.css:125–133` assigns radius and a 2px outward focus ring to every input, select, and textarea. Several fields instead place their visible border on an outer wrapper. The nested input starts after a currency prefix or search icon, so its outline does not follow the visible field boundary. The screenshot demonstrates this in Modify Order.

**Category:** accessibility, theming, responsive geometry.

**Impact:** keyboard/text focus appears inset or offset, and may form two competing highlighted boundaries. The same shared markup affects multiple order surfaces and search tools.

| Active family | Baseline style and markup | Required ownership |
| --- | --- | --- |
| `.modify-price-input-row` / `.modify-price-input` | `globals.css:7523–7563`; `ModifyOrderModal.tsx:729`; also SingleLegOrderTicket, OptionsChainTab, OrderTab, PositionTradeTicket | Wrapper owns ring and radius, including currency prefix and complete quantity field. |
| `.table-search` / `.table-search-input` | `globals.css:9395–9416`; `TableSearch.tsx:24–34` | Wrapper owns input-focus ring; clear button retains its own keyboard ring. |
| `.theta-search` / `.theta-search__input` | `globals.css:14101–14131`; ScannerTickerSearch, ThetaHarvesterScanner, StrengthConfirmationScanner | Wrapper owns input-focus ring; Scan button retains its own ring. |
| `.flow-ticker-input-row` / input | `globals.css:18149–18182`; `FlowAnalysisTickerInput.tsx:38–51` | Wrapper owns input-focus ring, including search icon; submit button retains its own ring. |
| `.command-palette-input-wrap` / `.command-palette-input` | `globals.css:16166–16185`; `CommandPalette.tsx:189–204` | Input row owns an inset ring because the enclosing palette clips overflow. |
| `.ask-composer` / `.ask-composer__input` | `globals.css:21149–21178`; `clear.css:518–526`; `AskComposer.tsx:264–319` | Composer owns textarea-focus ring; preserve its 10px Clear radius and individual button/select focus. |

AskComposer already suppresses its inner ring inside `.chat-panel`; the missing piece there is a consistent outer ring. Outside that context the generic rule can still outline the textarea. This is not evidence that the production chat panel currently shows the exact Modify Order defect.

**Recommendation:** use explicit active-family selectors to move the token-based focus ring to the bordered wrapper only when its input/textarea matches `:focus-visible`. Suppress the inner ring only for these paired controls. Do not use a broad descendant rule that removes focus from child buttons, selects, or unrelated inputs. Use an inset ring on the palette row to avoid its known overflow boundary. Normalize the four ordinary composite wrapper radii to `--radius-sm`; preserve the composer radius and palette geometry. Suggested command: `/normalize`.

### P2: instrument search overrides the shared keyboard ring inline

**Location:** `web/components/TickerSearch.tsx:421` sets `outline: "none"`, which outranks the Clear stylesheet. Lines 425–431 still change its border color on focus and blur.

**Category:** accessibility, theming.

**Impact:** this standalone input has a weaker 1px border-only indicator instead of the shared 2px keyboard ring. It is not an entirely invisible focus state.

**Recommendation:** remove the inline outline suppression and retain the existing focus/blur border behavior. The input already owns its complete visible border, so it should inherit the standard ring directly. Suggested command: `/normalize`.

## Exclusions and positive findings

- `OrderPriceInput` (`globals.css:9015–9018`) and `MobileOrderTicket` (`globals.css:17786–17804`) own borders on their actual inputs. Their row containers also contain separate controls or prefixes; transferring focus to those rows would introduce a new defect.
- `.regime-rail__filter input:focus` (`globals.css:10446`) contains `outline: none`, but ties the Clear selector's specificity and normally loses because `layout.tsx` imports Clear last. Its focus border is also visible. No current missing-ring bug is confirmed; leave it unchanged.
- Legacy `.chat-input-row` / `.chat-textarea` styles have no active TSX references. They are not counted as an affected production family and need no speculative cleanup.
- Standalone select, textarea, checkbox, button, and link rings remain appropriate. CSS module rules for news sharing and options controls do not suppress their indicators.
- Semantic `--border-focus` tokens already support both themes. Existing focus handling does not require adding state, event listeners, or dependencies.
- The command palette's clipping boundary and the composer's nested controls are explicit exceptions; preserving them prevents a shared fix from erasing meaningful individual focus targets.

## Verification plan

Planned, not yet passed: GitHub-run regression checks for all six active composite families; Modify Order geometry with a currency prefix; standalone instrument search; light and dark themes; desktop and mobile widths; keyboard transitions from input to neighboring clear/scan/send controls; unchanged standalone input/select/textarea focus; and palette overflow containment. Capture browser screenshots with animations disabled. No live order submission is needed.

Apply `/normalize` to the confirmed findings, then `/polish` through the focused browser matrix. Record actual CI and screenshot results in `tasks/todo.md` before completion; this baseline audit does not claim that verification has passed.
