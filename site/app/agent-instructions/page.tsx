import type { Metadata } from "next";
import { AgentDocument } from "@/components/sections/AgentDocument";
import {
  agentInstructionsMetadata,
  agentInstructionsPage,
} from "@/lib/developer-pages";

export const metadata: Metadata = agentInstructionsMetadata;

export default function AgentInstructionsPage() {
  return <AgentDocument page={agentInstructionsPage} />;
}
