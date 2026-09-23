/**
 * Simple rate limiter that queues function calls and processes
 * up to maxPerSecond per 1-second interval.
 *
 * Usage:
 *   const limiter = new RateLimiter(50);
 *   const result = await limiter.submit(() => doWork());
 */
export class RateLimiter {
  /**
   * @param {number} maxPerSecond — maximum calls to process per 1-second tick
   * @param {{ maxQueue?: number }} [options] — submissions beyond maxQueue reject
   */
  constructor(maxPerSecond, { maxQueue = Infinity } = {}) {
    this._max = maxPerSecond;
    this._maxQueue = maxQueue;
    this._queue = [];
    this._timer = null;
  }

  /**
   * Submit a function for rate-limited execution.
   * Returns a promise that resolves with the function's return value.
   * @param {() => *} fn
   * @returns {Promise<*>}
   */
  submit(fn) {
    return new Promise((resolve, reject) => {
      if (this._queue.length >= this._maxQueue) {
        reject(new Error("rate limiter queue full"));
        return;
      }
      this._queue.push({ fn, resolve, reject });
      this._drain();
    });
  }

  /** @private */
  _drain() {
    if (this._timer) return;
    this._timer = setInterval(() => {
      const batch = this._queue.splice(0, this._max);
      if (batch.length === 0) {
        clearInterval(this._timer);
        this._timer = null;
        return;
      }
      for (const item of batch) {
        try {
          item.resolve(item.fn());
        } catch (e) {
          item.reject(e);
        }
      }
    }, 1000);
  }

  /** Number of queued (not yet executed) items */
  get pending() {
    return this._queue.length;
  }

  /** Clear all pending items and stop the drain timer */
  clear() {
    this._queue = [];
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }
}
