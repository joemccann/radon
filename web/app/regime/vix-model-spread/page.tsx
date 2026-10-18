import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/vix-model-spread");

export default function RegimeVixModelSpreadPage() {
  return <WorkspaceShell section="regime" />;
}
