import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/divyield");

export default function RegimeDivYieldPage() {
  return <WorkspaceShell section="regime" />;
}
