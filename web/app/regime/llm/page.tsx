import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/llm");

export default function RegimeLlmPage() {
  return <WorkspaceShell section="regime" />;
}
