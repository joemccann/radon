/** The standalone gateway owns model discovery, availability and defaults. */
import { radonFetch } from "@/lib/radonApi";
import type { LlmProviderName } from "./provider";

export type LlmModelProvider = Exclude<LlmProviderName, "grok">;
export type LlmModelOption = {
  id: string; provider: LlmModelProvider; label: string; refreshedAt: string;
  capabilities?: { tools?: boolean; images?: boolean; structuredOutput?: boolean };
};
export type LlmCatalogSource = "gateway" | "turso" | "disk" | "builtin";
export type LlmModelCatalog = { models: LlmModelOption[]; defaultId: string; source: LlmCatalogSource };
const PROVIDERS = ["anthropic", "xai", "openai", "antigravity", "cursor", "nvidia", "cerebras", "slm-tagger"];

export async function resolveModelCatalog(): Promise<LlmModelCatalog> {
  const value = await radonFetch<LlmModelCatalog>("/model-gateway/models", { cache: "no-store" });
  if (!value || !Array.isArray(value.models) || typeof value.defaultId !== "string" ||
      !["gateway", "turso", "disk", "builtin"].includes(value.source) ||
      value.models.some(m => !m || typeof m.id !== "string" || !m.id || !PROVIDERS.includes(m.provider) || typeof m.label !== "string" || typeof m.refreshedAt !== "string") ||
      new Set(value.models.map(m => m.id)).size !== value.models.length ||
      (value.models.length > 0 ? !value.models.some(m => m.id === value.defaultId) : value.defaultId !== "")) {
    throw new Error("Model gateway catalog is malformed");
  }
  return value;
}

export async function validateModelId(id: unknown): Promise<LlmModelOption | null> {
  if (typeof id !== "string" || !id.trim()) return null;
  return (await resolveModelCatalog()).models.find(model => model.id === id.trim()) ?? null;
}
