import SetupWizard from "@/components/setup/SetupWizard";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/setup");

export const dynamic = "force-dynamic";

export default function SetupPage() {
  return <SetupWizard />;
}
