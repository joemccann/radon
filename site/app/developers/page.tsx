import type { Metadata } from "next";
import { AgentDocument } from "@/components/sections/AgentDocument";
import { developersMetadata, developersPage } from "@/lib/developer-pages";

export const metadata: Metadata = developersMetadata;

export default function DevelopersPage() {
  return <AgentDocument page={developersPage} />;
}
