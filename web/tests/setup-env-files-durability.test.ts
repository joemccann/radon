/**
 * REL-191 (R-523, R-524, R-546): the wizard's env writes are dotenv-safe,
 * atomic, and rewrite every duplicate occurrence of a managed key.
 *
 * The declared consumers are @next/env (web/.env) and python-dotenv (root
 * .env) — NOT a shell. The old `'\''` POSIX idiom truncates in both parsers
 * (python-dotenv drops the whole statement), and @next/env runs
 * dotenv-expand, which expands `$var` even inside single quotes. The
 * web-dialect round-trip is executed against the REAL @next/env below; the
 * root-dialect encodings are pinned here byte-exactly and round-tripped
 * through the REAL python-dotenv in
 * scripts/tests/test_rel191_root_env_dotenv_roundtrip.py — the two files
 * together close the loop.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { loadEnvConfig } from "@next/env";
import {
  upsertEnvContent,
  writeSetupEnvFiles,
} from "../lib/setup/envFiles";

const AWKWARD = "RX$ab'cd"; // the documented incident class: special chars
const noLog = { info: () => {}, error: () => {} };

async function tmpdir(): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), "radon-envfiles-"));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("web dialect round-trips through the real @next/env", () => {
  const cases: Record<string, string> = {
    UW_TOKEN: AWKWARD,
    PLAIN_KEY: "plain-value-123",
    DOLLAR_KEY: "pre$fix${HOME}post",
    QUOTE_KEY: 'va"lue',
    SINGLE_KEY: "va'lue",
  };

  it("every value reads back byte-identical", async () => {
    const content = upsertEnvContent("", cases, "next");
    const dir = await tmpdir();
    await fs.writeFile(path.join(dir, ".env"), content);
    const parsed = loadEnvConfig(dir, true, noLog, true).combinedEnv as Record<
      string,
      string
    >;
    for (const [key, value] of Object.entries(cases)) {
      expect(parsed[key], key).toBe(value);
    }
  });

  it("refuses a newline instead of silently corrupting the file", () => {
    expect(() => upsertEnvContent("", { K: "a\nb" }, "next")).toThrow(/newline/i);
  });
});

describe("root dialect is python-dotenv-safe", () => {
  // These pins are the other half of the loop closed by
  // scripts/tests/test_rel191_root_env_dotenv_roundtrip.py — if an encoding
  // here changes, that file's fixtures must change with it.
  it("single-quotes plain and dollar values", () => {
    const content = upsertEnvContent("", { A: "plain", B: "RX$ab" }, "python");
    expect(content).toContain("A='plain'");
    expect(content).toContain("B='RX$ab'");
  });

  it("double-quotes values containing a single quote, escaping for python-dotenv", () => {
    const content = upsertEnvContent("", { K: AWKWARD }, "python");
    expect(content).toContain('K="RX$ab\'cd"');
  });

  it("escapes backslash and double-quote inside double quotes", () => {
    const content = upsertEnvContent("", { K: `a\\b"c'd` }, "python");
    expect(content).toContain('K="a\\\\b\\"c\'d"');
  });

  it("refuses the un-encodable single-quote + interpolation combination", () => {
    expect(() =>
      upsertEnvContent("", { K: "x'y${HOME}z" }, "python"),
    ).toThrow(/cannot be safely written/i);
  });

  it("refuses newlines", () => {
    expect(() => upsertEnvContent("", { K: "a\nb" }, "python")).toThrow(
      /newline/i,
    );
  });
});

describe("duplicate keys are all rewritten (R-546)", () => {
  it("a doubled key loads the wizard's value under last-assignment-wins", () => {
    const existing = "UW_TOKEN='old-1'\nOTHER='x'\nUW_TOKEN='old-2'\n";
    const next = upsertEnvContent(existing, { UW_TOKEN: "fresh" }, "python");
    expect(next).not.toContain("old-2");
    expect(next).not.toContain("old-1");
    for (const line of next.split("\n")) {
      if (line.startsWith("UW_TOKEN=")) expect(line).toBe("UW_TOKEN='fresh'");
    }
  });
});

describe("writes are atomic (R-524)", () => {
  it("a crash mid-write leaves the original file intact", async () => {
    const tmp = await tmpdir();
    await fs.mkdir(path.join(tmp, "web"));
    const rootEnv = path.join(tmp, ".env");
    const original = "MENTHORQ_USER='keep-me'\nMENTHORQ_PASS='keep-too'\n";
    await fs.writeFile(rootEnv, original);

    // Crash injection: the first write call writes half its bytes to its
    // target and dies. Pre-fix the target was the live .env (O_TRUNC), so
    // the operator's existing secrets were destroyed.
    const realWriteFile = fs.writeFile.bind(fs);
    let crashed = false;
    const spy = vi
      .spyOn(fs, "writeFile")
      .mockImplementation(async (file: any, data: any, opts: any) => {
        if (!crashed && typeof data === "string") {
          crashed = true;
          await realWriteFile(file, data.slice(0, 5), opts);
          throw new Error("injected crash mid-write");
        }
        return realWriteFile(file, data, opts);
      });

    await expect(
      writeSetupEnvFiles({ UW_TOKEN: "new-value" }, tmp),
    ).rejects.toThrow(/injected crash/);
    spy.mockRestore();

    const after = await fs.readFile(rootEnv, "utf8");
    expect(after).toBe(original);
  });

  it("two concurrent completes lose neither write", async () => {
    const tmp = await tmpdir();
    await fs.mkdir(path.join(tmp, "web"));
    const rootEnv = path.join(tmp, ".env");
    await fs.writeFile(rootEnv, "");

    // Widen the read-modify-write window so an unserialized implementation
    // interleaves: both tabs read the empty file, and one write clobbers
    // the other.
    const realReadFile = fs.readFile.bind(fs);
    vi.spyOn(fs, "readFile").mockImplementation(async (...args: any[]) => {
      const result = await realReadFile(...(args as [any, any]));
      await new Promise((resolve) => setTimeout(resolve, 25));
      return result;
    });

    await Promise.all([
      writeSetupEnvFiles({ MENTHORQ_USER: "tab-one" }, tmp),
      writeSetupEnvFiles({ MENTHORQ_PASS: "tab-two" }, tmp),
    ]);
    vi.restoreAllMocks();

    const after = await fs.readFile(rootEnv, "utf8");
    expect(after).toContain("tab-one");
    expect(after).toContain("tab-two");
  });
});

describe("REL-218 (R-593): the upsert is quote-continuation aware", () => {
  const multiline = [
    'MENTHORQ_NOTES="first line',
    "UW_TOKEN=inside-the-quoted-block",
    'last line"',
    "UW_TOKEN=old-real-value",
    "",
  ].join("\n");

  it("never rewrites a continuation line inside a multiline quoted value", () => {
    const next = upsertEnvContent(multiline, { UW_TOKEN: "new-value" }, "python");
    const lines = next.split("\n");
    expect(lines[1]).toBe("UW_TOKEN=inside-the-quoted-block");
    expect(lines[2]).toBe('last line"');
    expect(lines[3]).toBe("UW_TOKEN='new-value'");
  });

  it("round-trips a multiline value through the real @next/env", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "radon-env-"));
    const target = path.join(dir, ".env");
    const existing = [
      'NOTES="line one',
      "SENTINEL=not-an-assignment",
      'line three"',
      "UW_TOKEN=old",
      "",
    ].join("\n");
    await fs.writeFile(target, existing);
    const next = upsertEnvContent(existing, { UW_TOKEN: "fresh" }, "next");
    await fs.writeFile(target, next);
    const noLog = { info: () => {}, error: () => {} };
    const parsed = loadEnvConfig(dir, true, noLog, true).combinedEnv as Record<string, string>;
    expect(parsed.NOTES).toBe("line one\nSENTINEL=not-an-assignment\nline three");
    expect(parsed.UW_TOKEN).toBe("fresh");
    expect(parsed.SENTINEL).toBeUndefined();
  });

  it("rewriting a managed key whose OLD value is multiline consumes its continuation", () => {
    const existing = [
      'UW_TOKEN="old line one',
      'old line two"',
      "OTHER=keep",
      "",
    ].join("\n");
    const next = upsertEnvContent(existing, { UW_TOKEN: "flat" }, "python");
    expect(next).not.toContain("old line two");
    const lines = next.split("\n");
    expect(lines[0]).toBe("UW_TOKEN='flat'");
    expect(lines[1]).toBe("OTHER=keep");
  });
});

describe("R-623: the two env files are written as a pair", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rolls the root .env back when the web write fails", async () => {
    const dir = await tmpdir();
    await fs.mkdir(path.join(dir, "web"), { recursive: true });
    const rootEnv = path.join(dir, ".env");
    await fs.writeFile(rootEnv, "EXISTING=keep\n", "utf8");
    const before = await fs.readFile(rootEnv, "utf8");

    const realRename = fs.rename.bind(fs);
    vi.spyOn(fs, "rename").mockImplementation(async (from: any, to: any) => {
      if (String(to).includes(`${path.sep}web${path.sep}.env`)) {
        const err: NodeJS.ErrnoException = new Error("EACCES: permission denied");
        err.code = "EACCES";
        throw err;
      }
      return realRename(from, to);
    });

    await expect(
      writeSetupEnvFiles(
        { CLERK_SECRET_KEY: "sk_test_x", NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_x" },
        dir,
      ),
    ).rejects.toThrow();

    // The half-written pair is exactly the isAuthMisconfigured shape the
    // middleware turns into a terminal error page, with no way back to /setup.
    expect(await fs.readFile(rootEnv, "utf8")).toBe(before);
  });

  it("still writes both files on the happy path", async () => {
    const dir = await tmpdir();
    await fs.mkdir(path.join(dir, "web"), { recursive: true });
    const written = await writeSetupEnvFiles(
      { CLERK_SECRET_KEY: "sk_test_x", NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_x" },
      dir,
    );
    expect(written).toHaveLength(2);
    expect(await fs.readFile(path.join(dir, ".env"), "utf8")).toContain("CLERK_SECRET_KEY");
    expect(await fs.readFile(path.join(dir, "web", ".env"), "utf8")).toContain(
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
    );
  });

  it("removes a root .env it created when the web write fails", async () => {
    const dir = await tmpdir();
    await fs.mkdir(path.join(dir, "web"), { recursive: true });
    const rootEnv = path.join(dir, ".env");

    const realRename = fs.rename.bind(fs);
    vi.spyOn(fs, "rename").mockImplementation(async (from: any, to: any) => {
      if (String(to).includes(`${path.sep}web${path.sep}.env`)) {
        throw new Error("ENOSPC");
      }
      return realRename(from, to);
    });

    await expect(
      writeSetupEnvFiles({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_x" }, dir),
    ).rejects.toThrow();
    await expect(fs.readFile(rootEnv, "utf8")).rejects.toThrow();
  });
});
