import { notFound, redirect } from "next/navigation";
import WorkspaceShell from "@/components/WorkspaceShell";
import { routeMetadata } from "@/lib/pageTitle";

const TICKER_RE = /^[A-Za-z]{1,5}$/;

type Props = {
  params: Promise<{ ticker: string }>;
};

export async function generateMetadata({ params }: Props) {
  const { ticker } = await params;
  return routeMetadata(`/flow-analysis/${ticker}`);
}

export default async function FlowAnalysisTickerPage({ params }: Props) {
  const { ticker: raw } = await params;

  if (!TICKER_RE.test(raw)) {
    return notFound();
  }

  const upper = raw.toUpperCase();
  if (raw !== upper) {
    redirect(`/flow-analysis/${upper}`);
  }

  return <WorkspaceShell section="flow-analysis" tickerParam={upper} />;
}
