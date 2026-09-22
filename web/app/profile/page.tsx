import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/profile");

export const dynamic = "force-dynamic";

export default function ProfilePage() {
  return <WorkspaceShell section="profile" />;
}
