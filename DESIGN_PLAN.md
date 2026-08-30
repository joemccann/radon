# Design Implementation Plan: Radon Chat modal (ChatLauncher)

## Summary

- **Scope:** component (the global ⌘J chat overlay's empty state + composer chrome)
- **Target:** `web/components/ChatLauncher.tsx`, `web/components/ChatPanel.tsx`, `web/components/agent/AskComposer.tsx`, `web/app/globals.css`
- **Winner variant:** A "Composer-led", with the SUGGESTED label removed
- **Key improvements:** one hero element (the focused input at the top), suggestions demoted from five boxed cards to slim `/cmd + description` rows, all secondary chrome (@ sources, / commands, model, esc) collapsed into a single muted mono footer line, Enter sends so the ASK button goes away, panel height fits content instead of filling the viewport.

## Final layout (empty state)

```
+--------------------------------------------------+
| [| Ask about flow, risk, structure...        ↵ ] |  <- focused input, signal ring
|                                                  |
|  /portfolio      Positions, exposure, margin ... |
|  /scan --top 12  Ranked flow signals across ...  |
|  /compare support vs against   Dark pool ...     |
|  /review watch list  Freshness and signal ...    |
|  /help           Every command and what it reads |
|--------------------------------------------------|
| @ SCOPE AN INSTRUMENT  / COMMANDS    GROK 4.6 · ESC |
+--------------------------------------------------+
```

Once a conversation exists, the transcript layout stays as today (composer at the bottom); the composer-first arrangement applies to `messages.length === 0` only.

## Files to Change

- [ ] `web/lib/data.ts` — extend `quickPromptsBySection` entries (or add a parallel map) with a one-line `desc` per prompt; descriptions used in the lab: portfolio "Positions, exposure, margin headroom", scan "Ranked flow signals across the watchlist", compare "Dark pool prints for and against the thesis", review watch list "Freshness and signal state per ticker", help "Every command and what it reads".
- [ ] `web/components/ChatPanel.tsx` — empty state: drop `.chat-empty-state__title` ("Ask Radon") and `__copy` blocks and the boxed `.chat-empty-card` list; render `AskComposer` FIRST, then the suggestion rows (`/cmd` mono + muted desc, no label above them). Non-empty state unchanged.
- [ ] `web/components/agent/AskComposer.tsx` — remove the ASK button (Enter submits; keep a subtle `↵` affordance inside the field); move `@ SOURCES` / `/ COMMANDS` chips and the MODEL picker into one muted mono footer row. Model label stays clickable to open the existing picker (selection must remain reachable — do not remove `onModelChange` plumbing).
- [ ] `web/components/ChatLauncher.tsx` — drop the `chat-launcher__head` bar in the empty state (the footer line carries "esc dismisses"); panel width `min(640px, 100%)`, height fits content when empty (remove `align-items: stretch` behavior for the empty state), full transcript height once messages exist.
- [ ] `web/app/globals.css` — new `.chat-empty-*` / composer-footer rules per the lab CSS (`clab-a__*` block is the reference: focus ring via `--border-focus` + 3px `--wash-signal` shadow, rows hover `--bg-panel-raised`, footer 10px mono uppercase `--text-muted`); delete orphaned `.chat-empty-card*` rules.

## Component API

- `ChatPanel` props unchanged. `AskComposer` gains optional `variant?: "hero" | "docked"` if the top-vs-bottom placement needs different chrome; prefer pure CSS if markup can stay identical.
- Suggestion row click keeps calling `sendMessage(prompt, [], selectedModelId)` exactly as `.chat-empty-card` does today.

## Required UI States

- **Empty:** composer-first layout above.
- **Focused:** input carries the signal ring by default on open (⌘J already focuses via `focusKey`).
- **Busy:** Enter disabled while `isBusy`; existing EngineTrace/typing states untouched.
- **Error:** `.chat-error` strip unchanged.
- **Conversation:** transcript + bottom composer + `.chat-pills` unchanged.

## Accessibility Checklist

- [ ] Suggestion rows are `<button type="button">` with full-row hit area (min 40px desktop `--hit-min`)
- [ ] Enter-to-send announced via `aria-keyshortcuts` or visible `↵` hint; ASK button removal must not orphan `aria-label`s
- [ ] Focus ring uses `--border-focus`; footer text ≥ AA on `--bg-panel` (`--text-muted` passes post-2026-07-14 releveling)
- [ ] Esc dismiss behavior unchanged (`ChatLauncher` keydown)

## Testing Checklist (red/green, per repo TDD rule)

- [ ] Update `chat-conversational-surface.test.tsx`: empty state renders composer before suggestions, no "ASK RADON" title, no SUGGESTED label
- [ ] Suggestion row click sends the prompt with the mirrored model id (existing pill test pattern)
- [ ] Enter in composer submits; Shift+Enter newline preserved if currently supported
- [ ] `model-picker.test.tsx`: picker still reachable from the footer row
- [ ] `chat-launcher-focus.test.tsx`: ⌘J focus behavior intact
- [ ] Live browser verification (chrome-cdp primary) + screenshot, dark and light

## Design Tokens

- Existing only: `--border-focus`, `--wash-signal`, `--bg-panel(-raised)`, `--text-muted/secondary/primary`, `--font-mono`, `--radius` (4px cap). No new tokens, no raw hex.

---

*Generated by the design-lab skill, 2026-08-29. Lab route and `.claude-design` chat-modal artifacts cleaned up (the concurrent /design-lab-headlines lab was left in place).*
