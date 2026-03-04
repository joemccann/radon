import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import type { OrdersData } from "@/lib/types";

export const runtime = "nodejs";

const TIMEOUT_MS = 15_000;

const resolveProjectRoot = (): string => {
  const candidates = [
    process.cwd(),
    path.resolve(process.cwd(), ".."),
    path.resolve(process.cwd(), "..", ".."),
  ];
  for (const candidate of candidates) {
    if (existsSync(path.join(candidate, "data"))) return candidate;
  }
  return process.cwd();
};

const readOrders = async (root: string): Promise<OrdersData | null> => {
  const filePath = path.join(root, "data", "orders.json");
  if (!existsSync(filePath)) return null;
  const content = await readFile(filePath, "utf8");
  return JSON.parse(content) as OrdersData;
};

type ModifyBody = {
  orderId?: number;
  permId?: number;
  newPrice?: number;
};

const runScript = (
  root: string,
  args: string[],
): Promise<{ ok: boolean; stdout: string; stderr: string }> => {
  return new Promise((resolve) => {
    const scriptPath = path.join("scripts", "ib_order_manage.py");
    const proc = spawn("python3", [scriptPath, ...args], {
      cwd: root,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    proc.stdout?.on("data", (chunk) => { stdout += chunk.toString(); });
    proc.stderr?.on("data", (chunk) => { stderr += chunk.toString(); });

    const timer = setTimeout(() => proc.kill("SIGKILL"), TIMEOUT_MS);

    proc.on("close", (code) => {
      clearTimeout(timer);
      resolve({ ok: code === 0, stdout, stderr });
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve({ ok: false, stdout: "", stderr: "Failed to spawn ib_order_manage.py" });
    });
  });
};

const runSync = (root: string): Promise<void> => {
  return new Promise((resolve) => {
    const scriptPath = path.join("scripts", "ib_orders.py");
    const proc = spawn("python3", [scriptPath, "--sync", "--port", "4001", "--client-id", "11"], {
      cwd: root,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const timer = setTimeout(() => proc.kill("SIGKILL"), 30_000);
    proc.on("close", () => { clearTimeout(timer); resolve(); });
    proc.on("error", () => { clearTimeout(timer); resolve(); });
  });
};

export async function POST(request: Request): Promise<Response> {
  try {
    const body = (await request.json()) as ModifyBody;
    const orderId = body.orderId ?? 0;
    const permId = body.permId ?? 0;
    const newPrice = body.newPrice;

    if (orderId === 0 && permId === 0) {
      return NextResponse.json(
        { error: "Must provide orderId or permId" },
        { status: 400 },
      );
    }

    if (newPrice == null || newPrice <= 0) {
      return NextResponse.json(
        { error: "Must provide newPrice > 0" },
        { status: 400 },
      );
    }

    const root = resolveProjectRoot();
    const args = [
      "modify",
      "--order-id", String(orderId),
      "--perm-id", String(permId),
      "--new-price", String(newPrice),
      "--port", "4001",
    ];

    const result = await runScript(root, args);

    let parsed: Record<string, unknown> = {};
    try {
      parsed = JSON.parse(result.stdout);
    } catch {
      // stdout wasn't valid JSON
    }

    if (!result.ok) {
      return NextResponse.json(
        { error: (parsed.message as string) || "Modify failed", detail: parsed },
        { status: 502 },
      );
    }

    await runSync(root);
    const orders = await readOrders(root);

    return NextResponse.json({
      status: "ok",
      message: parsed.message || "Order modified",
      orders,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Modify failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
