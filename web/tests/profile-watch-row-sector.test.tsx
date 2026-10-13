// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

vi.mock("@clerk/nextjs", () => ({ useClerk: () => ({}), useUser: () => ({ user: null, isLoaded: true }) }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));

import { WatchRow } from "@/components/profile/ProfileContent";

afterEach(cleanup);

const noop = () => {};

describe("Profile watchlist row", () => {
  it("drops the sector column instead of printing a placeholder when sector is unknown", () => {
    const { container } = render(
      <ul>
        <WatchRow
          entry={{ id: "1", symbol: "URTY", sector: null, added_at: "2026-10-01" }}
          price={{ last: 66.85, close: 67.4 } as never}
          onOpen={noop}
          onRemove={noop}
        />
      </ul>,
    );
    const row = container.querySelector(".profile-watch")!;
    expect(row.querySelector(".profile-watch__sector")).toBeNull();
    expect(row.className).toContain("profile-watch--no-sector");
    expect(row.textContent).not.toContain("---");
    expect(row.textContent).toContain("66.85");
    expect(row.textContent).toContain("-0.82%");
  });

  it("keeps the sector when the entry carries one", () => {
    const { container } = render(
      <ul>
        <WatchRow
          entry={{ id: "2", symbol: "NVDA", sector: "Technology", added_at: "2026-10-01" }}
          onOpen={noop}
          onRemove={noop}
        />
      </ul>,
    );
    expect(container.querySelector(".profile-watch__sector")?.textContent).toBe("Technology");
    expect(container.querySelector(".profile-watch--no-sector")).toBeNull();
  });
});
