import { DEFAULT_HOST, DEFAULT_LANG, DEFAULT_URL } from "./protocol.js";

export function buildUrl({ url, lang = DEFAULT_LANG } = {}) {
  if (url) return url;
  return `${DEFAULT_HOST}?lang=${lang}`;
}

function takeValue(argv, i) {
  const next = argv[i + 1];
  if (next == null || next.startsWith("--")) {
    throw new Error(`missing value for ${argv[i]}`);
  }
  return next;
}

export function parseArgs(argv) {
  const opts = {
    url: null,
    lang: DEFAULT_LANG,
    all: false,
    pretty: false,
    seconds: null,
    max: null,
    serve: false,
    port: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--all") opts.all = true;
    else if (arg === "--pretty") opts.pretty = true;
    else if (arg === "--serve") opts.serve = true;
    else if (arg === "--url") {
      opts.url = takeValue(argv, i);
      i += 1;
    } else if (arg === "--lang") {
      opts.lang = takeValue(argv, i);
      i += 1;
    } else if (arg === "--seconds") {
      opts.seconds = Number(takeValue(argv, i));
      i += 1;
    } else if (arg === "--max") {
      opts.max = Number(takeValue(argv, i));
      i += 1;
    } else if (arg === "--port") {
      opts.port = Number(takeValue(argv, i));
      i += 1;
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  if (opts.seconds != null && (!Number.isFinite(opts.seconds) || opts.seconds <= 0)) {
    throw new Error("--seconds must be a positive number");
  }
  if (opts.max != null && (!Number.isInteger(opts.max) || opts.max <= 0)) {
    throw new Error("--max must be a positive integer");
  }
  if (opts.port != null && (!Number.isInteger(opts.port) || opts.port <= 0)) {
    throw new Error("--port must be a positive integer");
  }
  opts.url = buildUrl(opts);
  return opts;
}

export { DEFAULT_URL };
