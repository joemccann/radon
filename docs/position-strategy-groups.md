# Position strategy groups

The Positions page supports a saved display layout for multi-leg option holdings. Grouping does not change Interactive Brokers inventory, execute orders, or rewrite the portfolio snapshot. Account totals and order coverage continue to use the canonical portfolio.

## Organize a holding

Open **Group strategies** beside a position:

- **Suggested split** recognizes an equal-quantity, same-expiry bull call spread and separates remaining legs. Review both groups before applying.
- **Select legs** lets you choose the calls and create a spread with a toolbar action. Unselected legs remain visible in their own groups.
- **Edit strategies** assigns every held leg to a named group. Each leg must appear exactly once. Group names do not determine risk classification.

Desktop uses a compact position menu and a side-panel editor. Mobile uses a bottom sheet for grouping and a full-screen editor. Every path previews quantities, signed basis and P&L before applying. Cancel leaves the current layout intact. **Undo grouping** restores the prior layout for the current session; **Reset grouping** restores the original holding.

## Persistence and measurement

Layouts are saved on this browser, keyed by account and stable held-contract identities. They survive reloads, sync refreshes and reordered legs. A changed quantity, direction, expiry, identity or ambiguous match invalidates the saved layout and shows the original holding. Legacy snapshots without an account identifier use memory-only layouts.

Displayed subsets retain their own legs and use only their verified signed basis and marks. Parent aggregate daily P&L, broker margin/return-capital metadata and trade targets are never allocated to children. Missing or mixed measurements remain unavailable. Known same-expiry bull call spreads can enter Defined Risk Positions; short puts remain in Undefined Risk Positions. Renaming a group never changes its economic classification. Custom groups cannot infer exact risk capital from a net debit; their own complete, consistent leg basis can still support the explicitly labeled net-entry-debit display return.

Grouping preferences are independent of the canonical position store. Resetting the layout is sufficient to roll back a grouping; reverting the feature does not require any broker action.
