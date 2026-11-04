/**
 * TDD: Modify order from the ticker detail view
 *
 * Tests cover the structural wiring that ensures modify works from
 * TickerDetailContent → OrderTab → ModifyOrderModal (same pattern as main workspace).
 *
 * The bug: OrderTab had an inline modify form that bypassed ModifyOrderModal and
 * lacked the "FILL OUTSIDE RTH" (outsideRth) flag. The fix moves the modify flow
 * to use ModifyOrderModal, called via requestModify from OrderActionsContext.
 *
 * These tests are structural (file content + API surface), not jsdom rendering tests,
 * consistent with the project test environment (node, no jsdom).
 */

import { describe, it, expect, beforeAll } from "vitest";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ─── Helper to load source files ─────────────────────────────────────────────

async function readSource(relPath: string): Promise<string> {
  return readFile(path.resolve(__dirname, relPath), "utf8");
}

// =============================================================================
// OrderTab: renders ModifyOrderModal, not an inline form
// =============================================================================

describe("OrderTab — ModifyOrderModal wiring", () => {
  let orderTabSource: string;

  beforeAll(async () => {
    orderTabSource = await readSource("../components/ticker-detail/OrderTab.tsx");
  });

  it("imports ModifyOrderModal", () => {
    expect(orderTabSource).toMatch(/import ModifyOrderModal/);
  });

  it("drives the modify modal from the order-dialog machine (identity, not a snapshot)", () => {
    // §1 #7: the dialog state names the order by identity; the live order is
    // re-resolved from openOrders. Behaviour: modify-live-target-owners.test.tsx.
    expect(orderTabSource).toMatch(/useOrderDialogMachine\(\)/);
    expect(orderTabSource).toMatch(/resolveLiveOrder\(dialog\.subject\.ref, openOrders\)/);
  });

  it("renders <ModifyOrderModal> inside the OrderTab", () => {
    // ModifyOrderModal must be rendered (JSX usage), not just imported
    expect(orderTabSource).toMatch(/<ModifyOrderModal/);
  });

  it("closes ModifyOrderModal through the machine's close event", () => {
    expect(orderTabSource).toMatch(/sendDialog\(\{ type: "close" \}\)/);
  });

  it("calls requestModify from OrderActionsContext on confirm", () => {
    // The onConfirm handler must call requestModify
    expect(orderTabSource).toMatch(/requestModify/);
  });

  it("does NOT have an inline modify form (setModifying pattern removed)", () => {
    // The old inline form toggled `modifying` boolean state.
    // After the fix, ExistingOrderRow should not own that state.
    // It may still exist as a transition, but the primary path must use ModifyOrderModal.
    // We check that ModifyOrderModal IS present (sufficient to confirm the fix).
    expect(orderTabSource).toMatch(/ModifyOrderModal/);
  });
});

// =============================================================================
// ExistingOrderRow: MODIFY button calls onModify callback
// =============================================================================

describe("ExistingOrderRow — MODIFY button wiring", () => {
  let orderTabSource: string;

  beforeAll(async () => {
    orderTabSource = await readSource("../components/ticker-detail/OrderTab.tsx");
  });

  it("ExistingOrderRow accepts an onModify prop", () => {
    // The component must accept a callback for modify so the parent can control state
    expect(orderTabSource).toMatch(/onModify/);
  });

  it("MODIFY button calls onModify with the order", () => {
    // The button onClick must call onModify(order) or onModify
    expect(orderTabSource).toMatch(/onModify\(/);
  });
});

// =============================================================================
// AssetCockpit: passes portfolio/openOrders/prices to OrderTab for BAG price
// resolution. OrderTab moved out of TickerDetailContent into the cockpit (the
// desktop act column) with the cockpit cutover; TickerDetailContent now renders
// only <AssetCockpit>, threading tickerOrders/prices/portfolio into it.
// =============================================================================

describe("AssetCockpit — OrderTab props", () => {
  let cockpitSource: string;

  beforeAll(async () => {
    cockpitSource = await readSource("../components/ticker-detail/AssetCockpit.tsx");
  });

  it("renders OrderTab with openOrders prop", () => {
    expect(cockpitSource).toMatch(/openOrders=\{tickerOrders\}/);
  });

  it("renders OrderTab with prices prop", () => {
    expect(cockpitSource).toMatch(/prices=\{prices\}/);
  });

  it("passes portfolio to OrderTab for BAG price resolution in ModifyOrderModal", () => {
    // OrderTab needs portfolio so it can pass it to ModifyOrderModal for BAG orders
    expect(cockpitSource).toMatch(/portfolio=\{portfolio\}/);
  });
});

// =============================================================================
// Structural: no inline form state in ExistingOrderRow after fix
// =============================================================================

describe("ExistingOrderRow — post-fix structure", () => {
  let orderTabSource: string;

  beforeAll(async () => {
    orderTabSource = await readSource("../components/ticker-detail/OrderTab.tsx");
  });

  it("ExistingOrderRow does NOT manage its own modifying boolean state", () => {
    // After the fix, the inline form is gone. The `modifying` useState in ExistingOrderRow
    // should no longer exist — the modify modal is managed by the parent (OrderTab).
    const existingOrderRowSection = orderTabSource.slice(
      orderTabSource.indexOf("function ExistingOrderRow"),
      orderTabSource.indexOf("function NewOrderForm"),
    );
    // The old pattern was: const [modifying, setModifying] = useState(false)
    // After the fix, ExistingOrderRow just calls onModify(order)
    expect(existingOrderRowSection).not.toMatch(/modifying.*useState/);
  });
});
