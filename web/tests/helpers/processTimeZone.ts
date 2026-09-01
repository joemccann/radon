import { afterAll, beforeAll } from "vitest";

/**
 * Run the enclosing `describe` with the process timezone set to `zone`, then
 * restore the suite's `TZ=America/New_York` pin (vitest.config.ts).
 *
 * The pin makes "ET" and "process-local" indistinguishable, so an "is ET, not
 * process-local" test that runs under it cannot red a process-local
 * implementation (T-319). Node re-reads `TZ` on the next Date construction;
 * `probe` asserts the override actually took effect so a silent no-op cannot
 * masquerade as proof of ET-independence.
 */
export function useProcessTimeZone(
  zone: string,
  probe: { iso: string; localHour: number },
): void {
  let previous: string | undefined;

  beforeAll(() => {
    previous = process.env.TZ;
    process.env.TZ = zone;
    const hour = new Date(probe.iso).getHours();
    if (hour !== probe.localHour) {
      throw new Error(
        `process TZ override to ${zone} did not take effect: ${probe.iso} reads hour ${hour}, expected ${probe.localHour}`,
      );
    }
  });

  afterAll(() => {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  });
}
