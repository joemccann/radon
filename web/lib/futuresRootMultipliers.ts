/** Multipliers from scripts/clients/contract_resolver.py FUTURES_ROOTS.
 *  Keys are those roots only. ES, NQ, and RTY are the futures symbols, not keys.
 *  Do not invent entries. Anything else still resolves through /futures/chain. */
export const FUTURES_ROOT_MULTIPLIERS: Readonly<Record<string, number>> = {
  VIX: 1000,
  SPX: 50,
  NDX: 20,
  RUT: 50,
};
