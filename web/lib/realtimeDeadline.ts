export const REALTIME_AUTH_TIMEOUT_MS = 8_000;
export const REALTIME_OPEN_TIMEOUT_MS = 8_000;
export const REALTIME_HEALTH_TIMEOUT_MS = 5_000;

export async function withRealtimeDeadline<T>(
  operation: Promise<T> | ((signal: AbortSignal) => Promise<T>),
  label: string,
  timeoutMs: number = REALTIME_AUTH_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | null = null;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error(`${label} timed out`));
    }, timeoutMs);
  });
  const promise = typeof operation === "function" ? operation(controller.signal) : operation;
  try {
    return await Promise.race([promise, deadline]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}
