import type { Metadata } from "next";
import { AgentDocument } from "@/components/sections/AgentDocument";
import { authMetadata, authPage } from "@/lib/developer-pages";

export const metadata: Metadata = authMetadata;

export default function AuthDocsPage() {
  return <AgentDocument page={authPage} />;
}
