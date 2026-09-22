# Radon iOS (stub)

No Xcode target lives here yet. This directory marks the eventual native iPhone
home. Do not add a project, CocoaPods/SPM lockfile, or App Store pipeline until
the design loop says so.

**Direction:** [`docs/mobile/iphone-app-direction.md`](../../docs/mobile/iphone-app-direction.md)

## Phases (do these in order)

1. **References** - collect brand plates, the live mobile order sheet, dark-pool
   surfaces, Chat, and IB status. Do not start from a prompt.
2. **AI draft** - first Chat / generator output is a draft, never final UI.
3. **Specific critique** - name layout, UX, color, and interaction failures
   against the direction doc.
4. **Figma taste lock** - lock frames before any implementation.
5. **Interactive MVP** - only after lock, build real flows here.

Trading actions stay propose-then-human-yes. Never auto-trade from AI.
