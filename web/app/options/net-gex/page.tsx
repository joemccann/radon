import WorkspaceShell from "@/components/WorkspaceShell";
import { metadataForPath } from "@/lib/pageTitle";

export const dynamic = "force-dynamic";

const SYMBOL_RE = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;

type Props = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ searchParams }: Props) {
  return metadataForPath("/options/net-gex", searchParams);
}

export default async function NetGexPage({ searchParams }: Props) {
  const params = await searchParams;
  const rawSymbol = Array.isArray(params.symbol) ? params.symbol[0] : params.symbol;
  const symbol = rawSymbol && SYMBOL_RE.test(rawSymbol) ? rawSymbol.toUpperCase() : undefined;

  return <WorkspaceShell section="options" tickerParam={symbol} />;
}
