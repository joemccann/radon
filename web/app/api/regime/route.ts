import { NextResponse } from "next/server";
import { readFile, writeFile } from "fs/promises";
import { join } from "path";
import { spawn } from "child_process";

export const runtime = "nodejs";

const CACHE_PATH = join(process.cwd(), "..", "data", "cri.json");
const SCRIPTS_DIR = join(process.cwd(), "..", "scripts");

function runCriScan(): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn("python3", ["cri_scan.py", "--json"], {
      cwd: SCRIPTS_DIR,
      timeout: 120_000,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
    proc.on("close", (code) => {
      if (code !== 0) reject(new Error(stderr || `cri_scan.py exited with code ${code}`));
      else resolve(stdout);
    });
    proc.on("error", reject);
  });
}

export async function GET(): Promise<Response> {
  try {
    const raw = await readFile(CACHE_PATH, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({
      scan_time: "",
      date: "",
      vix: 0,
      vvix: 0,
      spy: 0,
      vix_5d_roc: 0,
      vvix_vix_ratio: null,
      spx_100d_ma: null,
      spx_distance_pct: 0,
      avg_sector_correlation: null,
      corr_5d_change: null,
      realized_vol: null,
      cri: { score: 0, level: "LOW", components: { vix: 0, vvix: 0, correlation: 0, momentum: 0 } },
      cta: { realized_vol: 0, exposure_pct: 200, forced_reduction_pct: 0, est_selling_bn: 0 },
      menthorq_cta: null,
      crash_trigger: { triggered: false, conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, avg_correlation_gt_060: false }, values: {} },
      history: [],
    });
  }
}

export async function POST(): Promise<Response> {
  try {
    const stdout = await runCriScan();
    const jsonStart = stdout.indexOf("{");
    if (jsonStart === -1) throw new Error("No JSON output from cri_scan.py");
    const jsonStr = stdout.slice(jsonStart);
    const data = JSON.parse(jsonStr);

    await writeFile(CACHE_PATH, JSON.stringify(data, null, 2));

    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "CRI scan failed";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
