import { AsyncLocalStorage } from "node:async_hooks";

/** Stable identity shared by every observer of one logical DB operation. */
export type DbOperationIdentity = {
  readonly id: number;
  poolGeneration: number | null;
};

const operationStorage = new AsyncLocalStorage<DbOperationIdentity>();
let nextOperationId = 0;

export function createDbOperationIdentity(): DbOperationIdentity {
  nextOperationId += 1;
  return { id: nextOperationId, poolGeneration: null };
}

export function runWithDbOperation<T>(
  identity: DbOperationIdentity,
  operation: () => T,
): T {
  return operationStorage.run(identity, operation);
}

export function currentDbOperationIdentity(): DbOperationIdentity | undefined {
  return operationStorage.getStore();
}

export function markCurrentDbOperationPoolGeneration(generation: number): void {
  const identity = operationStorage.getStore();
  if (identity && identity.poolGeneration === null) {
    identity.poolGeneration = generation;
  }
}
