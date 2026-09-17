import WorkspaceShell from "@/components/WorkspaceShell";
import { metadataForPath } from "@/lib/pageTitle";

type Props = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ searchParams }: Props) {
  return metadataForPath("/scanner", searchParams);
}

export default function ScannerPage() {
  return <WorkspaceShell section="scanner" />;
}
