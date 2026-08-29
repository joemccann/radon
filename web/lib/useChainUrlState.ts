"use client";

/**
 * Deep-links the Options Chain tab filters (expiry / side / strikes) into the
 * URL so a selection is shareable and survives reload + back/forward.
 *
 * Query schema (all optional; defaults are omitted to keep `?tab=chain` clean):
 *   expiry=YYYY-MM-DD   dashed ISO; maps to the compact internal expiry
 *   side=calls|puts     omitted for the default "both" (ALL)
 *   strikes=10|25|50|100|all   omitted for the default 15
 *   legs=SELL:1x520P,SELL:1x625C   optional chain-builder prefill
 *
 * Mirrors the write/read mechanism in `useNewsfeedTagFilter.ts`: a post-commit
 * `router.replace(url, { scroll: false })` that clones the existing params (so
 * `tab` and any other params survive) and a `lastWrittenRef` loop-guard.
 */
import { useCallback, useMemo, useRef } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { formatExpiry, ALL_STRIKES } from "@/lib/optionsChainUtils";

export type SideFilter = "both" | "calls" | "puts";

const SIDE_PARAM = "side";
const STRIKES_PARAM = "strikes";
const EXPIRY_PARAM = "expiry";
const LEGS_PARAM = "legs";
const SRC_PARAM = "src";

const ALLOWED_STRIKES = [10, 15, 25, 50, 100] as const;
const DEFAULT_STRIKES = 15;

export type ChainLegUrlDraft = {
  action: "BUY" | "SELL";
  quantity: number;
  strike: number;
  right: "C" | "P";
};

export function parseSideParam(raw: string | null | undefined): SideFilter {
  if (raw === "calls" || raw === "puts") return raw;
  return "both"; // absent, "all", or unknown
}

export function parseStrikesParam(raw: string | null | undefined): number {
  if (raw === "all") return ALL_STRIKES;
  const n = Number(raw);
  return (ALLOWED_STRIKES as readonly number[]).includes(n) ? n : DEFAULT_STRIKES;
}

function serializeSide(side: SideFilter): string | null {
  return side === "both" ? null : side;
}

function serializeStrikes(n: number): string | null {
  if (n === DEFAULT_STRIKES) return null;
  if (n === ALL_STRIKES) return "all";
  return String(n);
}

export function parseLegsParam(raw: string | null | undefined): ChainLegUrlDraft[] {
  if (!raw) return [];
  const drafts: ChainLegUrlDraft[] = [];
  for (const segment of raw.split(",")) {
    const token = segment.trim().toUpperCase();
    if (!token) return [];
    const match = token.match(/^(BUY|SELL):(?:(\d+)X)?(\d+(?:\.\d+)?)([CP])$/);
    if (!match) return [];
    const quantity = match[2] == null ? 1 : Number(match[2]);
    const strike = Number(match[3]);
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 999) return [];
    if (!Number.isFinite(strike) || strike <= 0) return [];
    drafts.push({
      action: match[1] as "BUY" | "SELL",
      quantity,
      strike,
      right: match[4] as "C" | "P",
    });
  }
  return drafts;
}

export function serializeLegsParam(legs: ChainLegUrlDraft[]): string | null {
  if (legs.length === 0) return null;
  return legs
    .map((leg) => {
      const quantity = Math.max(1, Math.trunc(leg.quantity));
      return `${leg.action}:${quantity}x${leg.strike}${leg.right}`;
    })
    .join(",");
}

export interface ChainUrlState {
  /** Lazy `useState` seed for the side toggle. */
  initialSide: SideFilter;
  /** Lazy `useState` seed for the strikes dropdown. */
  initialStrikes: number;
  /** Raw dashed expiry from the URL (validated against `expirations` by caller). */
  urlExpiry: string | null;
  /** Optional chain-builder leg prefill from the URL. */
  urlLegs: ChainLegUrlDraft[];
  /** Raw param strings — dep keys for back/forward reconcile effects. */
  sideParamRaw: string | null;
  strikesParamRaw: string | null;
  legsParamRaw: string | null;
  /** Post-commit writer: pushes current filter state into the URL, preserving other params. */
  syncUrl: (state: { selectedExpiry: string | null; side: SideFilter; strikes: number }) => void;
}

export function useChainUrlState(): ChainUrlState {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const lastWrittenRef = useRef<string | null>(null);

  // Read-once seeds for the lazy useState initializers (mount-time hydration).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const initialSide = useMemo(() => parseSideParam(searchParams?.get(SIDE_PARAM)), []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const initialStrikes = useMemo(() => parseStrikesParam(searchParams?.get(STRIKES_PARAM)), []);

  const urlExpiry = searchParams?.get(EXPIRY_PARAM) ?? null;
  const sideParamRaw = searchParams?.get(SIDE_PARAM) ?? null;
  const strikesParamRaw = searchParams?.get(STRIKES_PARAM) ?? null;
  const legsParamRaw = searchParams?.get(LEGS_PARAM) ?? null;
  const urlLegs = useMemo(() => parseLegsParam(legsParamRaw), [legsParamRaw]);

  const syncUrl = useCallback<ChainUrlState["syncUrl"]>(
    (state) => {
      const expiryVal = state.selectedExpiry ? formatExpiry(state.selectedExpiry) : null;
      const sideVal = serializeSide(state.side);
      const strikesVal = serializeStrikes(state.strikes);
      const signature = `${expiryVal ?? ""}|${sideVal ?? ""}|${strikesVal ?? ""}`;

      // What the URL already encodes, normalized through the same serializers.
      const current = `${searchParams?.get(EXPIRY_PARAM) ?? ""}|${
        serializeSide(parseSideParam(searchParams?.get(SIDE_PARAM))) ?? ""
      }|${serializeStrikes(parseStrikesParam(searchParams?.get(STRIKES_PARAM))) ?? ""}`;

      if (signature === current) {
        lastWrittenRef.current = signature;
        return;
      }
      if (lastWrittenRef.current === signature) return; // already wrote this; awaiting URL settle
      lastWrittenRef.current = signature;

      const params = new URLSearchParams(searchParams?.toString() ?? "");
      const apply = (key: string, value: string | null) =>
        value == null ? params.delete(key) : params.set(key, value);
      apply(EXPIRY_PARAM, expiryVal);
      apply(SIDE_PARAM, sideVal);
      apply(STRIKES_PARAM, strikesVal);

      // R-378: the URL named an expiry the chain could not honour, so this write
      // is rewriting ?expiry to the fallback. Leaving ?legs and ?src behind means
      // that on the next pass the requested expiry MATCHES the fallback and the
      // prefill arms a contract nobody selected, under a ticket still naming the
      // scanner. A contract that was not honoured does not survive the rewrite.
      const requested = searchParams?.get(EXPIRY_PARAM) ?? null;
      if (requested && expiryVal && formatExpiry(requested) !== expiryVal) {
        params.delete(LEGS_PARAM);
        params.delete(SRC_PARAM);
      }

      const query = params.toString();
      const url = query ? `${pathname}?${query}` : pathname ?? "/";
      router.replace(url, { scroll: false });
    },
    [searchParams, pathname, router],
  );

  return { initialSide, initialStrikes, urlExpiry, urlLegs, sideParamRaw, strikesParamRaw, legsParamRaw, syncUrl };
}
