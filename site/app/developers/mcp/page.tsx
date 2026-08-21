import type { Metadata } from "next";
import { AgentDocument } from "@/components/sections/AgentDocument";
import { mcpMetadata, mcpPage } from "@/lib/developer-pages";

export const metadata: Metadata = mcpMetadata;

export default function McpDocsPage() {
  return <AgentDocument page={mcpPage} />;
}
