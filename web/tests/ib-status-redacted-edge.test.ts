/**
 * @vitest-environment jsdom
 *
 * F10 round 2 — the IB chip must survive the trust-split /status body.
 *
 * IBStatusContext polls /edge-health/status as the PRIMARY in production
 * precisely so the chip keeps reporting when radon-api / Next.js are down: that
 * surface is Caddy -> the isolated health daemon and shares no fate with the
 * trading stack. The daemon now redacts detail for public-edge callers, and the
 * browser IS a public-edge caller (it cannot carry the server-side bearer
 * without shipping the token to every client), so the redacted body has no
 * `probes` section and parseIbHealth returned null on every prod poll.
 *
 * The redacted body still publishes the aggregate the off-box prober validates
 * (`ok` / `overall_state`), and that aggregate is non-healthy whenever the
 * nested FastAPI payload reports awaiting_2fa / upstream_dead / an unhealthy
 * service state. So it can prove health, it just cannot attribute a fault:
 *
 *   - overall_state "up"  -> connected, definitively, with no extra request;
 *   - anything else       -> "unhealthy", and the caller asks the rich
 *                            /api/admin/health proxy WHY. When that proxy is
 *                            down too (the outage this surface exists for) the
 *                            coarse verdict stands instead of going blank.
 *
 * No new information leaves the daemon: overall_state is already public.
 */
import { describe, expect, it } from "vitest";

import {
  isAggregateOnlyHealthPayload,
  parseIbHealth,
} from "../lib/IBStatusContext";

const REDACTED_HEALTHY = {
  schema_version: 2,
  ok: true,
  overall_state: "up",
  generated_at: "2026-08-11T00:00:00+00:00",
  health_service: "ok",
};

const REDACTED_DEGRADED = { ...REDACTED_HEALTHY, ok: false, overall_state: "degraded" };

describe("parseIbHealth — redacted public-edge aggregate", () => {
  it("reads a healthy aggregate as connected", () => {
    expect(parseIbHealth(REDACTED_HEALTHY)).toEqual({
      authState: "authenticated",
      serviceState: "healthy",
      upstreamDead: false,
    });
  });

  it("reads a non-healthy aggregate as unhealthy rather than going blank", () => {
    expect(parseIbHealth(REDACTED_DEGRADED)).toEqual({
      authState: null,
      serviceState: "unhealthy",
      upstreamDead: null,
    });
    expect(parseIbHealth({ ...REDACTED_HEALTHY, ok: false, overall_state: "down" })).toEqual({
      authState: null,
      serviceState: "unhealthy",
      upstreamDead: null,
    });
  });

  it("never invents a broker state the redacted body does not carry", () => {
    // awaiting_2fa is exactly what the redaction removes; the coarse read must
    // not guess it, and must not claim authenticated while degraded either.
    expect(parseIbHealth(REDACTED_DEGRADED)?.authState).toBeNull();
  });

  it("still prefers the detailed sections when they are present", () => {
    const detailed = {
      ...REDACTED_HEALTHY,
      ok: false,
      overall_state: "degraded",
      probes: {
        "radon-api": {
          state: "up",
          payload: { auth_state: "awaiting_2fa", service_state: "healthy", upstream_dead: false },
        },
      },
    };
    expect(parseIbHealth(detailed)).toEqual({
      authState: "awaiting_2fa",
      serviceState: "healthy",
      upstreamDead: false,
    });
  });
});

describe("isAggregateOnlyHealthPayload — when to ask the rich proxy why", () => {
  it("is true only for a daemon body stripped of its detail sections", () => {
    expect(isAggregateOnlyHealthPayload(REDACTED_HEALTHY)).toBe(true);
    expect(
      isAggregateOnlyHealthPayload({ ...REDACTED_HEALTHY, probes: { "radon-api": { state: "up" } } }),
    ).toBe(false);
  });

  it("is false for anything that is not the daemon aggregate", () => {
    expect(isAggregateOnlyHealthPayload(null)).toBe(false);
    expect(isAggregateOnlyHealthPayload({})).toBe(false);
    expect(isAggregateOnlyHealthPayload({ ib_gateway: { auth_state: "authenticated" } })).toBe(false);
  });
});
