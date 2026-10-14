/**
 * Admission budget for the market-hours account poll.
 *
 * WorkspaceShell reads portfolio and orders every 2s while the equity
 * session is open and every 30s in extended hours. Three tabs share the
 * operator's portfolio GET bucket. Demo shell polls share one more bucket
 * with service health, up to three futures quotes, the flex probe, and a
 * risk-free remount. The ceilings are those cadences and nothing larger:
 * the next read in the same window is refused.
 */
export const MARKET_HOURS_ACCOUNT_POLL_MS = 2_000;
export const OFF_HOURS_ACCOUNT_POLL_MS = 30_000;
export const SHARED_ACCOUNT_TABS = 3;

/** Three open-market tabs: 3 * (60_000 / 2_000). */
export const PORTFOLIO_GET_LIMIT = 90;

/**
 * One open minute, one tab: portfolio + orders at 2s (30+30), service
 * health (1), three futures quotes at 30s (6), flex (1), risk-free
 * remount (1). Three tabs share the demo minute bucket.
 */
export const DEMO_SHELL_MINUTE_LIMIT = 207;

/**
 * Three tabs for one weekday: 390 regular minutes at the open rate plus
 * 570 extended minutes at the 30s account rate. Other shell polls do not
 * slow down off the open. 3 * (390 * 69 + 570 * 13).
 */
export const DEMO_SHELL_DAILY_LIMIT = 102_960;
