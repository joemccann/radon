import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

type CommandStatus = "ok" | "error";

type PiRoutePayload = {
  input?: string;
  command?: string;
  text?: string;
};

type ScriptResult = {
  command: string;
  status: CommandStatus;
  output: string;
  stderr: string;
  exitCode: number;
  timedOut: boolean;
  source: "script" | "local";
};

type PiResponse = {
  command: string;
  status: CommandStatus;
  output: string;
  stderr?: string;
  exitCode?: number;
  timedOut?: boolean;
  source?: ScriptResult["source"];
};

const MAX_CHAR_RESPONSE = 40_000;
const DEFAULT_TIMEOUT_MS = 120_000;

const PI_COMMANDS = ["help", "scan", "discover", "evaluate", "portfolio", "journal", "sync", "leap-scan"] as const;
type PiCommand = (typeof PI_COMMANDS)[number];

type ParsedCommand = {
  command: PiCommand;
  args: string[];
  rest: string[];
};

type Paths = {
  cwd: string;
  scriptsDir: string;
  dataDir: string;
};

const trimOutput = (value: string) => {
  if (!value) {
    return "";
  }

  const normalized = value.trim();
  if (normalized.length <= MAX_CHAR_RESPONSE) {
    return normalized;
  }

  return `${normalized.slice(0, MAX_CHAR_RESPONSE)}\n\n[... truncated ${normalized.length - MAX_CHAR_RESPONSE} chars ...]`;
};

const resolveProjectPaths = (): Paths => {
  const candidates = [process.cwd(), path.resolve(process.cwd(), ".."), path.resolve(process.cwd(), "..", "..")];

  for (const candidate of candidates) {
    const scriptsDir = path.join(candidate, "scripts");
    const dataDir = path.join(candidate, "data");
    if (existsSync(path.join(scriptsDir, "scanner.py")) && existsSync(path.join(dataDir, "portfolio.json"))) {
      return { cwd: candidate, scriptsDir, dataDir };
    }
  }

  return {
    cwd: process.cwd(),
    scriptsDir: path.join(process.cwd(), "scripts"),
    dataDir: path.join(process.cwd(), "data"),
  };
};

const readScriptablePaths = () => resolveProjectPaths();

const clamp = (value: string, maxLength = 200) => value.slice(0, maxLength).toUpperCase();

const isPositiveInt = (value: string) => Number.isInteger(Number(value)) && Number(value) >= 0;
const isTicker = (value: string) => /^[A-Z0-9.\-]{1,12}$/i.test(value);
const splitCommandTokens = (input: string) => input.trim().split(/\s+/).filter(Boolean);

const normalizeCommand = (value: string): ParsedCommand | null => {
  const tokens = splitCommandTokens(value);
  if (!tokens.length) {
    return null;
  }

  const rawCommand = tokens[0].toLowerCase().replace(/^\//, "");
  if (!PI_COMMANDS.includes(rawCommand as PiCommand)) {
    return null;
  }

  return {
    command: rawCommand as PiCommand,
    args: tokens.slice(1),
    rest: tokens.slice(1),
  };
};

const runPythonScript = (script: string, args: string[], cwd: string, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<ScriptResult> => {
  return new Promise((resolve) => {
    const pieces = [script, ...args];
    const proc = spawn("python3.13", pieces, { cwd, env: process.env, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let timedOut = false;

    if (proc.stdout) {
      proc.stdout.on("data", (chunk) => {
        stdout += chunk.toString();
      });
    }

    if (proc.stderr) {
      proc.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
      });
    }

    const onTimeout = () => {
      timedOut = true;
      proc.kill("SIGKILL");
    };

    const timer = setTimeout(onTimeout, timeoutMs);
    proc.on("close", (code) => {
      clearTimeout(timer);
      resolve({
        command: script,
        status: code === 0 && !timedOut ? "ok" : "error",
        output: trimOutput(stdout),
        stderr: trimOutput(stderr),
        exitCode: code ?? 1,
        timedOut,
        source: "script",
      });
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve({
        command: script,
        status: "error",
        output: "",
        stderr: "Failed to spawn python script.",
        exitCode: 1,
        timedOut,
        source: "script",
      });
    });
  });
};

const readLocalJsonFile = async <T>(filePath: string): Promise<T> => {
  const content = await readFile(filePath, "utf8");
  return JSON.parse(content) as T;
};

const formatPortfolio = (raw: unknown) => {
  const portfolio = raw as {
    bankroll?: number;
    position_count?: number;
    defined_risk_count?: number;
    undefined_risk_count?: number;
    last_sync?: string;
    positions?: unknown[];
  };

  const payload = {
    bankroll: portfolio?.bankroll ?? 0,
    position_count: portfolio?.position_count ?? 0,
    defined_risk_count: portfolio?.defined_risk_count ?? 0,
    undefined_risk_count: portfolio?.undefined_risk_count ?? 0,
    last_sync: portfolio?.last_sync ?? "unknown",
    positions:
      portfolio?.positions?.map((position: unknown) => {
        const entry = position as {
          ticker?: string;
          structure?: string;
          expiry?: string;
          risk_profile?: string;
          entry_cost?: number;
        };

        return {
          ticker: entry?.ticker ?? "unknown",
          structure: entry?.structure ?? "unknown",
          expiry: entry?.expiry ?? "N/A",
          risk_profile: entry?.risk_profile ?? "unknown",
          entry_cost: entry?.entry_cost ?? 0,
        };
      }) ?? [],
  };

  return JSON.stringify(payload, null, 2);
};

const formatJournal = (raw: unknown, limit?: number) => {
  const journal = (raw as { trades?: unknown[] }) ?? {};
  const trades = Array.isArray(journal.trades) ? journal.trades : [];
  const truncated = typeof limit === "number" ? trades.slice(0, limit) : trades;

  return JSON.stringify({ trades: truncated }, null, 2);
};

const parseFlagInt = (value: string, name: string) => {
  if (!isPositiveInt(value)) {
    throw new Error(`Invalid --${name} value: ${value}`);
  }
  return String(Number(value));
};

const parseEvaluate = (args: string[]) => {
  const [tickerArg, ...rest] = args;
  if (!tickerArg) {
    throw new Error("evaluate requires a ticker: /evaluate TICKER");
  }
  if (!isTicker(tickerArg)) {
    throw new Error("Invalid ticker.");
  }
  const parsed: { days?: string } = {};
  for (let i = 0; i < rest.length; i += 1) {
    const arg = rest[i];
    if (arg === "--days") {
      if (i + 1 >= rest.length) {
        throw new Error("Missing value for --days");
      }
      parsed.days = parseFlagInt(rest[i + 1], "days");
      i += 1;
      continue;
    }
    throw new Error(`Unknown flag for evaluate: ${arg}`);
  }
  return { ticker: clamp(tickerArg, 12), days: parsed.days };
};

export const buildEvaluateCommand = (args: string[]): string[] => {
  const { ticker, days } = parseEvaluate(args);
  return ["scripts/evaluate.py", ticker, "--days", days ?? "5"];
};

const parseGenericIntFlags = (args: string[], schema: Record<string, (value: string) => string>) => {
  const parsed: Record<string, string> = {};
  const positional: string[] = [];

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (!arg.startsWith("--")) {
      positional.push(arg);
      continue;
    }

    const parser = schema[arg];
    if (!parser) {
      throw new Error(`Unknown flag: ${arg}`);
    }

    if (i + 1 >= args.length) {
      throw new Error(`Missing value for ${arg}`);
    }

    const value = args[i + 1];
    parsed[arg.replace(/^--/, "")] = parser(value);
    i += 1;
  }

  return { parsed, positional };
};

const parseBooleanFlags = (args: string[], booleanSet: Set<string>) => {
  const parsed = new Set<string>();
  const positional: string[] = [];

  for (let i = 0; i < args.length; i += 1) {
    const token = args[i];
    if (!token.startsWith("--")) {
      positional.push(token);
      continue;
    }

    if (!booleanSet.has(token)) {
      throw new Error(`Unknown flag: ${token}`);
    }
    parsed.add(token);
  }

  return { parsed, positional };
};

const executePortfolio = async (paths: Paths): Promise<ScriptResult> => {
  const data = await readLocalJsonFile(path.join(paths.dataDir, "portfolio.json"));
  return {
    command: "portfolio",
    status: "ok",
    output: formatPortfolio(data),
    stderr: "",
    exitCode: 0,
    timedOut: false,
    source: "local",
  };
};

const executeJournal = (args: string[], paths: Paths): Promise<ScriptResult> => {
  const parsed = parseGenericIntFlags(args, {
    "--limit": (value) => parseFlagInt(value, "limit"),
  });
  const limit = parsed.parsed.limit ? Number(parsed.parsed.limit) : undefined;

  if (parsed.positional.length > 0) {
    throw new Error("journal accepts only --limit");
  }

  return readLocalJsonFile(path.join(paths.dataDir, "trade_log.json")).then((raw) => ({
    command: "journal",
    status: "ok",
    output: formatJournal(raw, limit),
    stderr: "",
    exitCode: 0,
    timedOut: false,
    source: "local",
  }));
};

const normalizeScanArgs = (args: string[]): string[] => {
  const tokens = args.slice();
  if (!tokens.length) return tokens;

  // Accept "scan 20" → "scan --top 20"
  if (tokens.length === 1 && isPositiveInt(tokens[0])) {
    return ["--top", tokens[0]];
  }

  // Accept "scan top 20" / "scan min-score 12" → "scan --top 20" / "scan --min-score 12"
  if (tokens.length === 2 && /^(top|min-score)$/i.test(tokens[0]) && isPositiveInt(tokens[1])) {
    return [`--${tokens[0].toLowerCase()}`, tokens[1]];
  }

  return tokens;
};

const executeScan = async (args: string[], paths: Paths): Promise<ScriptResult> => {
  const parsed = parseGenericIntFlags(normalizeScanArgs(args), {
    "--top": (value) => parseFlagInt(value, "top"),
    "--min-score": (value) => parseFlagInt(value, "min-score"),
  });

  if (parsed.positional.length) {
    throw new Error(
      `scan only accepts --top N or --min-score N (got: ${parsed.positional.join(" ")})`,
    );
  }

  const commandArgs = [path.join("scripts", "scanner.py")];
  if (parsed.parsed["top"]) {
    commandArgs.push("--top", parsed.parsed["top"]);
  }
  if (parsed.parsed["min-score"]) {
    commandArgs.push("--min-score", parsed.parsed["min-score"]);
  }

  return runPythonScript(commandArgs[0], commandArgs.slice(1), paths.cwd);
};

const executeDiscover = async (args: string[], paths: Paths): Promise<ScriptResult> => {
  const booleanFlags = new Set<string>(["--include-indices"]);
  const booleanParsed = parseBooleanFlags(args, booleanFlags);
  const integerFlags = parseGenericIntFlags(booleanParsed.positional, {
    "--min-premium": (value) => parseFlagInt(value, "min-premium"),
    "--min-alerts": (value) => parseFlagInt(value, "min-alerts"),
    "--dp-days": (value) => parseFlagInt(value, "dp-days"),
  });

  if (integerFlags.positional.length > 0) {
    throw new Error("discover does not accept positional tickers");
  }

  const commandArgs = [path.join("scripts", "discover.py")];
  if (integerFlags.parsed["min-premium"]) commandArgs.push("--min-premium", integerFlags.parsed["min-premium"]);
  if (integerFlags.parsed["min-alerts"]) commandArgs.push("--min-alerts", integerFlags.parsed["min-alerts"]);
  if (integerFlags.parsed["dp-days"]) commandArgs.push("--dp-days", integerFlags.parsed["dp-days"]);
  if (booleanParsed.parsed.has("--include-indices")) commandArgs.push("--include-indices");

  return runPythonScript(commandArgs[0], commandArgs.slice(1), paths.cwd);
};

const executeEvaluate = async (args: string[], paths: Paths): Promise<ScriptResult> => {
  const commandArgs = buildEvaluateCommand(args);

  const result = await runPythonScript(commandArgs[0], commandArgs.slice(1), paths.cwd);

  const status: CommandStatus = result.exitCode === 0 || !!result.output ? "ok" : "error";

  return {
    command: "evaluate",
    status,
    output: result.output,
    stderr: result.stderr,
    exitCode: result.exitCode,
    timedOut: result.timedOut,
    source: result.source,
  };
};

const executeSync = (args: string[], paths: Paths): Promise<ScriptResult> => {
  const booleanParsed = parseBooleanFlags(args, new Set(["--sync", "--no-prices"]));
  const parsed = parseGenericIntFlags(booleanParsed.positional, {
    "--port": (value) => parseFlagInt(value, "port"),
    "--client-id": (value) => parseFlagInt(value, "client-id"),
  });

  const commandArgs = ["scripts/ib_sync.py"];

  if (parsed.positional.length) {
    throw new Error("sync accepts only --sync, --no-prices, --port, --client-id");
  }

  if (parsed.parsed["client-id"]) commandArgs.push("--client-id", parsed.parsed["client-id"]);
  if (parsed.parsed["port"]) commandArgs.push("--port", parsed.parsed["port"]);
  if (booleanParsed.parsed.has("--sync")) commandArgs.push("--sync");
  if (booleanParsed.parsed.has("--no-prices")) commandArgs.push("--no-prices");

  return runPythonScript(commandArgs[0], commandArgs.slice(1), paths.cwd);
};

const executeLeapScan = (args: string[], paths: Paths): Promise<ScriptResult> => {
  const commandArgs = ["scripts/leap_scanner_uw.py"];

  for (let i = 0; i < args.length; i += 1) {
    const token = args[i];
    if (!token.startsWith("--")) {
      if (!isTicker(token)) {
        throw new Error(`Invalid ticker: ${token}`);
      }
      commandArgs.push(token);
      continue;
    }

    if (token === "--preset") {
      if (i + 1 >= args.length) {
        throw new Error("Missing value for --preset");
      }
      const preset = args[i + 1].toLowerCase();
      if (!["sectors", "mag7", "semis", "emerging", "china"].includes(preset)) {
        throw new Error(`Invalid preset: ${preset}`);
      }
      commandArgs.push("--preset", preset);
      i += 1;
      continue;
    }

    if (token === "--min-gap") {
      if (i + 1 >= args.length) {
        throw new Error("Missing value for --min-gap");
      }
      commandArgs.push("--min-gap", parseFlagInt(args[i + 1], "min-gap"));
      i += 1;
      continue;
    }

    if (token === "--json") {
      commandArgs.push("--json");
      continue;
    }
    throw new Error(`Unknown flag for leap-scan: ${token}`);
  }

  return runPythonScript(commandArgs[0], commandArgs.slice(1), paths.cwd);
};

const executeHelp = () =>
  Promise.resolve<ScriptResult>({
    command: "help",
    status: "ok",
    output:
      "Available PI commands: /scan, /discover, /evaluate TICKER, /portfolio, /journal, /sync, /leap-scan, /help.\n" +
      "Examples: /scan --top 20, /discover --min-premium 500000, /evaluate AAPL, /journal --limit 5",
    stderr: "",
    exitCode: 0,
    timedOut: false,
    source: "local",
  });

const executeCommand = async (value: ParsedCommand): Promise<ScriptResult> => {
  const paths = readScriptablePaths();

  switch (value.command) {
    case "help":
      return executeHelp();
    case "portfolio":
      return executePortfolio(paths);
    case "journal":
      return executeJournal(value.args, paths);
    case "scan":
      return executeScan(value.args, paths);
    case "discover":
      return executeDiscover(value.args, paths);
    case "evaluate":
      return executeEvaluate(value.args, paths);
    case "sync":
      return executeSync(value.args, paths);
    case "leap-scan":
      return executeLeapScan(value.args, paths);
    default:
      throw new Error(`Unknown command: ${value.command}`);
  }
};

const resolvePiInput = (body: PiRoutePayload): string => {
  const inputParts: string[] = [
    typeof body.command === "string" ? body.command.trim() : "",
    typeof body.text === "string" ? body.text.trim() : "",
    typeof body.input === "string" ? body.input.trim() : "",
  ].filter(Boolean);

  if (inputParts.length === 0) {
    return "";
  }

  if (inputParts.length === 1) {
    return inputParts[0];
  }

  const [first, second] = inputParts;
  const firstHasTokens = first.includes(" ") || first.includes("\n");
  const secondIsFlag = second.startsWith("-");

  if (!firstHasTokens && second && !secondIsFlag) {
    return `${first} ${second}`;
  }

  return first;
};

export const __resolvePiInput = resolvePiInput;
export const __normalizeScanArgs = normalizeScanArgs;

export async function POST(request: NextRequest): Promise<Response> {
  let body: PiRoutePayload;
  try {
    body = (await request.json()) as PiRoutePayload;
  } catch {
    return NextResponse.json({ error: "Invalid JSON payload." }, { status: 400 });
  }

  const normalizedInput = resolvePiInput(body);

  if (typeof normalizedInput !== "string" || !normalizedInput) {
    return NextResponse.json({ error: "Missing input text." }, { status: 400 });
  }

  const parsed = normalizeCommand(normalizedInput);
  if (!parsed) {
    return NextResponse.json(
      {
        error:
          "Only embedded PI commands are accepted. Try /help or one of: /scan, /discover, /evaluate, /portfolio, /journal, /sync, /leap-scan",
      },
      { status: 400 },
    );
  }

  try {
    const result = await executeCommand(parsed);
    const response: PiResponse = {
      command: parsed.command,
      status: result.status,
      output: result.output || "",
      exitCode: result.exitCode,
      stderr: result.stderr || undefined,
      timedOut: result.timedOut,
      source: result.source,
    };

    const status = result.status === "ok" ? 200 : 422;
    return NextResponse.json(response, { status });
  } catch (error) {
    const message = error instanceof Error ? error.message : "pi command failed";
    return NextResponse.json({ command: parsed.command, status: "error", output: message }, { status: 400 });
  }
}
