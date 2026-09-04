import { describe, expect, it } from "vitest";

import { isBpiPayload } from "@/lib/bpi";
import { lastCompletedSessionDate, mostRecentSessionDate } from "@/lib/marketSession";
import { scanTimeToEtDate } from "@/lib/parseScanTime";
import {
  buildDemoBpiFixture,
  buildDemoCriFixture,
  buildDemoDispersionFixture,
  buildDemoGammaRotationFixture,
  buildDemoGexFixture,
  buildDemoTrinFixture,
  buildDemoVcgFixture,
} from "@/lib/demo/fixtures/regime";

describe("deterministic demo regime fixtures", () => {
  const now = new Date("2031-02-10T18:45:00.000Z");
  const expectedSession = mostRecentSessionDate(now);

  it("is stable for an injected clock and follows the current ET session", () => {
    const first = {
      cri: buildDemoCriFixture(now),
      vcg: buildDemoVcgFixture(now),
      gammaRotation: buildDemoGammaRotationFixture(now),
      gex: buildDemoGexFixture(now),
      dispersion: buildDemoDispersionFixture(now),
      trin: buildDemoTrinFixture(now),
      bpi: buildDemoBpiFixture(now),
    };
    const second = {
      cri: buildDemoCriFixture(now),
      vcg: buildDemoVcgFixture(now),
      gammaRotation: buildDemoGammaRotationFixture(now),
      gex: buildDemoGexFixture(now),
      dispersion: buildDemoDispersionFixture(now),
      trin: buildDemoTrinFixture(now),
      bpi: buildDemoBpiFixture(now),
    };

    expect(second).toEqual(first);
    expect(first.cri.date).toBe(expectedSession);
    expect(scanTimeToEtDate(first.cri.scan_time)).toBe(expectedSession);
    expect(scanTimeToEtDate(first.vcg.scan_time)).toBe(expectedSession);
    expect(scanTimeToEtDate(first.gammaRotation.scan_time)).toBe(expectedSession);
    expect(scanTimeToEtDate(first.gex.scan_time)).toBe(expectedSession);
    expect(first.dispersion.data_date).toBe(lastCompletedSessionDate(now));
    expect(first.trin.current?.session_date).toBe(expectedSession);
  });

  it("contains representative non-empty data for every regime panel", () => {
    const cri = buildDemoCriFixture(now);
    const vcg = buildDemoVcgFixture(now);
    const gammaRotation = buildDemoGammaRotationFixture(now);
    const gex = buildDemoGexFixture(now);
    const dispersion = buildDemoDispersionFixture(now);
    const trin = buildDemoTrinFixture(now);
    const bpi = buildDemoBpiFixture(now);

    expect(cri.cri?.score).toBeGreaterThan(0);
    expect(cri.history.length).toBeGreaterThanOrEqual(20);
    expect(vcg.signal?.vcg_adj).not.toBeNull();
    expect(vcg.history.length).toBeGreaterThanOrEqual(20);
    expect(gammaRotation.signal.grg_z).not.toBeNull();
    expect(gammaRotation.history.length).toBeGreaterThanOrEqual(20);
    expect(gex.spot).toBeGreaterThan(0);
    expect(gex.profile.length).toBeGreaterThanOrEqual(5);
    expect(dispersion.current).not.toBeNull();
    expect(dispersion.series.length).toBeGreaterThanOrEqual(60);
    expect(trin.current?.trin).not.toBeNull();
    expect(trin.hourly.length).toBeGreaterThanOrEqual(20);
    expect(Object.values(bpi.indices).every(isBpiPayload)).toBe(true);
  });

  it("keeps each summary coherent with its final series observation", () => {
    const cri = buildDemoCriFixture(now);
    const vcg = buildDemoVcgFixture(now);
    const gammaRotation = buildDemoGammaRotationFixture(now);
    const gex = buildDemoGexFixture(now);
    const dispersion = buildDemoDispersionFixture(now);
    const trin = buildDemoTrinFixture(now);

    expect(cri.history.at(-1)).toMatchObject({ date: cri.date, vix: cri.vix, spy: cri.spy });
    expect(vcg.history.at(-1)).toMatchObject({ vcg_adj: vcg.signal?.vcg_adj, credit: vcg.signal?.credit_price });
    expect(gammaRotation.history.at(-1)).toMatchObject({
      date: gammaRotation.data_date,
      grg_z: gammaRotation.signal.grg_z,
      raw_spread: gammaRotation.signal.raw_spread,
      spy_gamma_z: gammaRotation.signal.spy_gamma_z,
      tlt_gamma_z: gammaRotation.signal.tlt_gamma_z,
    });
    expect(gammaRotation.signal.raw_spread).toBe(
      (gammaRotation.signal.spy_gamma_z ?? 0) - (gammaRotation.signal.tlt_gamma_z ?? 0),
    );
    expect(gammaRotation.signal.spy_3d_gamma_change).toBeGreaterThan(0);
    expect(gammaRotation.assets.SPY.gamma_3d_change).toBe(
      gammaRotation.signal.spy_3d_gamma_change,
    );
    expect(gammaRotation.history.at(-1)?.state).toBe("RISK_OFF_DIVERGENCE");
    for (const point of gammaRotation.history) {
      const spy = point.spy_net_gamma ?? 0;
      const tlt = point.tlt_net_gamma ?? 0;
      const expectedState = spy > 0 && tlt < 0
        ? "RISK_ON_DIVERGENCE"
        : spy < 0 && tlt > 0
          ? "RISK_OFF_DIVERGENCE"
          : spy > 0 && tlt > 0
            ? "DUAL_CUSHION"
            : "DUAL_WHIP";
      expect(point.state).toBe(expectedState);
    }
    expect(gex.history.at(-1)).toMatchObject({
      date: gex.data_date,
      net_gex: gex.net_gex,
      spot: gex.spot,
      gex_flip: gex.levels.gex_flip?.strike,
    });
    for (const name of ["max_magnet", "second_magnet", "max_accelerator", "put_wall", "call_wall"] as const) {
      const level = gex.levels[name];
      expect(gex.profile.find((bucket) => bucket.strike === level?.strike)?.net_gex).toBe(level?.gamma);
    }
    expect(dispersion.current).toMatchObject(dispersion.series.at(-1) ?? {});
    expect(trin.hourly.at(-1)).toMatchObject({
      ts: trin.current?.ts,
      trin: trin.current?.trin,
      ma10: trin.current?.ma10,
    });
    expect(
      ((trin.current?.adv ?? 0) / (trin.current?.dec ?? 1))
      / ((trin.current?.up_vol ?? 0) / (trin.current?.down_vol ?? 1)),
    ).toBeCloseTo(trin.current?.trin ?? 0, 2);
  });
});
