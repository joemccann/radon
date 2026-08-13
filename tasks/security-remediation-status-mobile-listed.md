# Mobile, listed-contract, and admin remediation status

| Finding | Status | Patch | Regression |
|---|---|---|---|
| HB-077 | Fixed | Mobile stop review, risk, notional, success, and submitted payload share the order-type-resolved stop price. | `web/tests/mobile-order-ticket.test.tsx` STP price case |
| HB-078 | Fixed | Signed natural combo quotes are preserved; manual entry is blocked while debit/credit sign is unresolved. | `web/tests/mobile-order-ticket.test.tsx` missing-quote combo case; `options-chain-utils.test.ts` |
| HB-080 | Fixed | Listed-contract submit requires the branded risk state for the current input; omitted portfolio remains pending. | `web/tests/listed-order-security.test.tsx` pending futures case |
| HB-081 | Fixed | Futures selection resets by symbol and contract/data identity is revalidated at build and submit. | `web/tests/listed-order-security.test.tsx` stale futures case |
| HB-082 | Fixed | Index-option submission requires current resolved portfolio risk. | `web/tests/listed-order-security.test.tsx` index permit case |
| HB-083 | Fixed | Index expiry/right/conId reset by symbol; stale chain payloads and mismatched contracts cannot submit. | `web/tests/listed-order-security.test.tsx` cross-ticker case |
| HB-091 | Fixed | New and combo order forms remount on ticker/position identity change, invalidating edits and confirmation. | keyed form contract in `OrderTab.tsx`; typecheck |
| BUG-084 | Fixed | An unreachable control plane renders gateway power unknown and disables actions. | `web/tests/admin-components.test.tsx` unknown gateway case |
| BUG-085 | Fixed | Power callbacks must explicitly confirm success; absent/failed calls never alter optimistic state. | `web/tests/admin-components.test.tsx` callback failure case |
| BUG-090 | Fixed | Mobile action sheets retain stable permIds and resolve the latest row immediately before modify/cancel. | `web/tests/mobile-order-list-display.test.tsx` refreshed combo case |
| BUG-091 | Fixed | Futures risk matches holdings by conId and passes held long/short quantities plus proportional close basis. | `web/tests/listed-order-security.test.tsx` futures close case |

Reconciliation: 11/11 fixed; 0 duplicate; 0 actionable.
