import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/cta");

export default function CtaPage() {
  return <WorkspaceShell section="cta" />;
}
