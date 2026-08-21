import type { Metadata } from "next";
import { AgentDocument } from "@/components/sections/AgentDocument";
import { openapiMetadata, openapiPage } from "@/lib/developer-pages";

export const metadata: Metadata = openapiMetadata;

export default function OpenApiDocsPage() {
  return <AgentDocument page={openapiPage} />;
}
