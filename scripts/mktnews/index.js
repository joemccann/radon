#!/usr/bin/env node
/**
 * Tap the MKTNews flash websocket and print frames.
 *
 *   node scripts/mktnews/index.js
 *   node scripts/mktnews/index.js --pretty
 *   node scripts/mktnews/index.js --serve
 *   node scripts/mktnews/index.js --all --seconds 20
 *   node scripts/mktnews/index.js --max 5 --lang zh
 *
 * Default: JSONL on stdout, time heartbeats omitted. Status on stderr.
 */
import { parseArgs } from "./cli.js";
import { connectMktnews } from "./client.js";
import { formatMessage } from "./format.js";
import { DEFAULT_LISTEN_PORT, startHeadlinesHub } from "./hub.js";
import { createHeadlinesStore } from "./store.js";

export function runTap({
  argv = process.argv.slice(2),
  stdout = process.stdout,
  stderr = process.stderr,
  connect = connectMktnews,
  exit = process.exit,
  setTimer = setTimeout,
} = {}) {
  const opts = parseArgs(argv);
  const controller = new AbortController();
  let emitted = 0;
  let client;

  const stop = (code = 0) => {
    client?.stop();
    controller.abort();
    exit(code);
  };

  client = connect({
    url: opts.url,
    signal: controller.signal,
    onStatus: (event) => {
      if (event.event === "open") {
        stderr.write(`[mktnews] open ${event.url}\n`);
      } else if (event.event === "close") {
        stderr.write(`[mktnews] close ${event.code} ${event.reason}\n`);
      } else if (event.event === "reconnect") {
        stderr.write(`[mktnews] reconnect attempt=${event.attempt} delayMs=${event.delayMs}\n`);
      } else if (event.event === "error") {
        stderr.write(`[mktnews] error ${event.error}\n`);
      }
    },
    onMessage: (msg) => {
      const line = formatMessage(msg, { pretty: opts.pretty, all: opts.all });
      if (line == null) return;
      stdout.write(`${line}\n`);
      emitted += 1;
      if (opts.max != null && emitted >= opts.max) stop(0);
    },
  });

  if (opts.seconds != null) {
    setTimer(() => stop(0), opts.seconds * 1000);
  }

  return { stop, controller };
}

function resolveRingStore() {
  if (process.env.TURSO_DB_URL) return createHeadlinesStore();
  process.stderr.write("[mktnews] TURSO_DB_URL unset; ring will not survive restarts\n");
  return null;
}

const isMain = process.argv[1] && process.argv[1].endsWith("mktnews/index.js");
if (isMain) {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.serve) {
    const hub = await startHeadlinesHub({
      listenPort: opts.port ?? DEFAULT_LISTEN_PORT,
      store: resolveRingStore(),
    });
    process.stderr.write(`[mktnews] hub ${hub.address()}\n`);
    const shutdown = () => {
      hub.stop().finally(() => process.exit(0));
    };
    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);
  } else {
    const handle = runTap();
    process.on("SIGINT", () => handle.stop(0));
    process.on("SIGTERM", () => handle.stop(0));
  }
}
