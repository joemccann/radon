import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/journal");

export default function JournalPage() {
  return <WorkspaceShell section="journal" />;
}
