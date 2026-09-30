import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/rsi-oversold");

export default function RegimeRsiOversoldPage() {
  return <WorkspaceShell section="regime" />;
}
