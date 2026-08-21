import type { Metadata } from "next";
import { AgentDocument } from "@/components/sections/AgentDocument";
import { webhooksMetadata, webhooksPage } from "@/lib/developer-pages";

export const metadata: Metadata = webhooksMetadata;

export default function WebhooksDocsPage() {
  return <AgentDocument page={webhooksPage} />;
}
