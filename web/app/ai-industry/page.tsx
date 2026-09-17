import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/ai-industry");

export default function AiIndustryPage() {
  return <WorkspaceShell section="ai-industry" />;
}
