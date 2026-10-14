import type { PortfolioLeg, PortfolioPosition } from "@/lib/types";
import { resolvePositionTradeTarget } from "@/lib/order/positionTrade";
import { getLegMultiplier } from "@/lib/positionUtils";

/** Browser display preferences. They never describe an executable broker order. */
export type StrategyGroup = { id: string; name: string; legIds: string[] };
export type StrategyGroupPlan = {
  sourceKey: string;
  fingerprint: string;
  persistent: boolean;
  groups: StrategyGroup[];
};
export type StrategyLeg = { id: string; index: number; leg: PortfolioLeg };

function expiryOf(source: PortfolioPosition, leg: PortfolioLeg): string | null {
  if (leg.type === "Stock") return "";
  const raw = leg.expiry || source.expiry;
  if (typeof raw !== "string") return null;
  const expiry = raw.replace(/-/g, "");
  if (!/^\d{8}$/.test(expiry)) return null;
  const year = Number(expiry.slice(0, 4));
  const month = Number(expiry.slice(4, 6));
  const day = Number(expiry.slice(6, 8));
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day ? expiry : null;
}

/** An ambiguous or malformed snapshot cannot be partitioned. */
export function describeStrategyLegs(source: PortfolioPosition): StrategyLeg[] {
  if (typeof source.ticker !== "string" || !source.ticker.trim() || !Array.isArray(source.legs) || !source.legs.length) return [];
  const seen = new Set<string>();
  const conIds = new Set<number>();
  const result: StrategyLeg[] = [];
  for (const [index, leg] of source.legs.entries()) {
    if (!leg || typeof leg !== "object") return [];
    const expiry = expiryOf(source, leg);
    if (expiry == null || !["Call", "Put"].includes(leg.type)
      || !["LONG", "SHORT"].includes(leg.direction)
      || !Number.isSafeInteger(leg.contracts) || leg.contracts <= 0
      || (leg.type !== "Stock" && (!Number.isFinite(leg.strike) || leg.strike! <= 0))
      || (leg.con_id != null && (!Number.isSafeInteger(leg.con_id) || leg.con_id <= 0))) return [];
    const economic = JSON.stringify([source.ticker.trim().toUpperCase(), leg.type, leg.strike, expiry]);
    // Two entries for one held contract have no stable assignment to a group.
    if (seen.has(economic) || (leg.con_id != null && conIds.has(leg.con_id))) return [];
    seen.add(economic);
    if (leg.con_id != null) conIds.add(leg.con_id);
    const id = JSON.stringify([leg.con_id ?? null, economic, leg.direction]);
    result.push({ id, index, leg });
  }
  return result;
}

export function strategySourceIdentity(source: PortfolioPosition): { key: string; fingerprint: string; persistent: boolean } | null {
  const legs = describeStrategyLegs(source);
  if (!legs.length) return null;
  if ((source.account_id != null && typeof source.account_id !== "string")
    || (source.position_instance_id != null && typeof source.position_instance_id !== "string")) return null;
  const account = source.account_id?.trim() || null;
  const identity = source.position_instance_id?.trim() || legs.map(leg => leg.id).sort();
  const key = JSON.stringify([account, source.ticker.toUpperCase(), identity]);
  const fingerprint = JSON.stringify([key, source.entry_date, legs.map(leg => [leg.id, leg.leg.contracts]).sort((a, b) => String(a[0]).localeCompare(String(b[0])))]);
  return { key, fingerprint, persistent: account != null };
}

export function defaultStrategyGroups(source: PortfolioPosition): StrategyGroup[] {
  const legs = describeStrategyLegs(source);
  return legs.length ? [{ id: "whole", name: source.structure || "Position", legIds: legs.map(leg => leg.id) }] : [];
}

/** Complete partition: every held leg occurs once, without quantity allocation. */
export function validateStrategyGroups(source: PortfolioPosition, value: unknown): StrategyGroup[] | null {
  const legs = describeStrategyLegs(source);
  if (!legs.length || !Array.isArray(value) || !value.length || value.length > legs.length) return null;
  const available = new Set(legs.map(leg => leg.id));
  const ids = new Set<string>();
  const assigned = new Set<string>();
  const groups: StrategyGroup[] = [];
  for (const input of value) {
    if (!input || typeof input !== "object" || typeof input.id !== "string" || typeof input.name !== "string"
      || !Array.isArray(input.legIds) || !input.legIds.length) return null;
    const id = input.id.trim();
    const name = input.name.trim();
    if (!id || id.length > 80 || ids.has(id) || !name || name.length > 80 || /[\x00-\x1f\x7f]/.test(id + name)) return null;
    ids.add(id);
    for (const legId of input.legIds) {
      if (typeof legId !== "string" || !available.has(legId) || assigned.has(legId)) return null;
      assigned.add(legId);
    }
    groups.push({ id, name, legIds: [...input.legIds] });
  }
  return assigned.size === available.size ? groups : null;
}

function bullCall(legs: PortfolioLeg[], source: PortfolioPosition): boolean {
  if (legs.length !== 2) return false;
  const long = legs.find(leg => leg.direction === "LONG");
  const short = legs.find(leg => leg.direction === "SHORT");
  return !!long && !!short && long.type === "Call" && short.type === "Call"
    && long.contracts === short.contracts && long.strike! < short.strike!
    && expiryOf(source, long) === expiryOf(source, short)
    && getLegMultiplier(long) === getLegMultiplier(short);
}

export function suggestStrategyGroups(source: PortfolioPosition): StrategyGroup[] | null {
  const legs = describeStrategyLegs(source);
  // Multiple valid pairings are ambiguous; leave that decision to the editor.
  const pairs: StrategyLeg[][] = [];
  for (let i = 0; i < legs.length; i++) for (let j = i + 1; j < legs.length; j++) {
    if (bullCall([legs[i].leg, legs[j].leg], source)) pairs.push([legs[i], legs[j]]);
  }
  if (pairs.length !== 1 || legs.length <= 2) return null;
  const pair = pairs[0];
  const paired = new Set(pair.map(leg => leg.id));
  return [{ id: "bull-call", name: "Bull Call Spread", legIds: pair.map(leg => leg.id) },
    ...legs.filter(leg => !paired.has(leg.id)).map((item, index) => ({
      id: `leg-${index}`, name: `${item.leg.direction === "LONG" ? "Long" : "Short"} ${item.leg.type}`,
      legIds: [item.id],
    }))];
}

/** Projection only; canonical source remains the sole broker/risk input. */
export function projectStrategyGroups(source: PortfolioPosition, value: unknown): PortfolioPosition[] | null {
  const sourceIdentity = strategySourceIdentity(source);
  if (!sourceIdentity) return null;
  const groups = validateStrategyGroups(source, value);
  if (!groups) return null;
  if (groups.length === 1) return [{ ...source, structure: groups[0].name }];
  const identities = describeStrategyLegs(source);
  const rows: PortfolioPosition[] = [];
  const usedIds = new Set([source.id]);
  for (const group of groups) {
    const indices = group.legIds.map(id => identities.find(leg => leg.id === id)!.index);
    const row = resolvePositionTradeTarget(source, { kind: "legs", indices });
    if (!row) return null;
    const expiries = new Set(row.legs.map(leg => expiryOf(source, leg)!));
    const expiry = [...expiries][0];
    row.expiry = expiries.size === 1 ? `${expiry.slice(0, 4)}-${expiry.slice(4, 6)}-${expiry.slice(6, 8)}` : "Mixed";
    row.structure = group.name;
    let hash = 2166136261;
    for (const char of `${sourceIdentity.key}:${group.id}`) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    row.id = -((hash >>> 0) + 1);
    while (usedIds.has(row.id)) row.id--;
    usedIds.add(row.id);
    row.risk_profile = "complex";
    row.strategy_group_capital_verified = false;
    if (bullCall(row.legs, source)) {
      row.structure_type = "Bull Call Spread";
      row.risk_profile = "defined";
      const width = Math.abs(row.legs[0].strike! - row.legs[1].strike!);
      const payoffWidth = width * row.contracts * getLegMultiplier(row.legs[0]);
      if (row.entry_cost != null && row.entry_cost > 0 && row.entry_cost <= payoffWidth
        && row.basis_source != null && row.basis_source !== "mixed") {
        row.max_risk = row.entry_cost;
        row.strategy_group_capital_verified = true;
      }
    } else if (row.legs.length === 1) {
      const leg = row.legs[0];
      row.structure_type = leg.type;
      row.risk_profile = leg.direction === "LONG" ? "defined" : "undefined";
      if (leg.direction === "LONG" && row.entry_cost != null && row.entry_cost > 0
        && row.basis_source != null && row.basis_source !== "mixed") {
        row.max_risk = row.entry_cost;
        row.strategy_group_capital_verified = true;
      }
    }
    rows.push(row);
  }
  return rows;
}
