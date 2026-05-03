/*
  Backwards-compat shim — historical chrome-cdp surface is preserved so the
  rest of the newsfeed module graph doesn't move. Real work happens against a
  Playwright Page provided by `setActivePage()` from index.js.
*/
const PLAYWRIGHT_TARGET_ID = "playwright-page";

let activePage = null;
let activeContext = null;

export function setActivePage(page, context) {
  activePage = page || null;
  activeContext = context || null;
}

export function getActivePage() {
  return activePage;
}

export async function listTargets() {
  if (!activePage) {
    throw new Error("No active Playwright page — call setActivePage(page) before listTargets().");
  }
  return [
    {
      targetId: PLAYWRIGHT_TARGET_ID,
      url: typeof activePage.url === "function" ? activePage.url() : "",
      type: "page",
    },
  ];
}

export function selectMarketEarTab(pages) {
  if (!Array.isArray(pages) || pages.length === 0) {
    throw new Error("selectMarketEarTab: no pages provided.");
  }
  return pages[0];
}

function expressionToEvaluator(expression) {
  if (typeof expression !== "string") {
    throw new Error("runCdpCommand eval expects a string expression.");
  }
  // The expression is an IIFE that returns a JSON string. Wrap as a function
  // body so page.evaluate() can run it inside the document context.
  return new Function(`return (${expression});`);
}

export async function runCdpCommand(command, _targetId, ...rest) {
  if (!activePage) {
    throw new Error("No active Playwright page — call setActivePage(page) first.");
  }

  if (command === "eval") {
    const expression = rest[0];
    const evaluator = expressionToEvaluator(expression);
    return await activePage.evaluate(evaluator);
  }

  if (command === "evalraw") {
    const [domainMethod, paramsJson] = rest;
    const params = paramsJson ? JSON.parse(paramsJson) : {};
    if (domainMethod === "Network.getCookies" && Array.isArray(params.urls)) {
      if (!activeContext || typeof activeContext.cookies !== "function") {
        throw new Error("Active Playwright context required for cookie lookup.");
      }
      const cookies = await activeContext.cookies(params.urls);
      return JSON.stringify({ cookies });
    }
    throw new Error(`runCdpCommand evalraw: unsupported method ${domainMethod}`);
  }

  if (command === "list") {
    return JSON.stringify(await listTargets());
  }

  throw new Error(`runCdpCommand: unsupported command "${command}"`);
}

export function formatCookieHeader(cookies) {
  if (!Array.isArray(cookies)) return "";
  return cookies
    .filter((c) => c && typeof c.name === "string" && typeof c.value === "string")
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
}

export async function fetchCookieHeader(_targetId, urls) {
  if (!activeContext || typeof activeContext.cookies !== "function") return "";
  try {
    const cookies = await activeContext.cookies(urls);
    return formatCookieHeader(cookies);
  } catch {
    return "";
  }
}

export const DEFAULT_CDP_PATH = null;
export function resolveCdpPath() {
  return null;
}
