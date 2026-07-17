import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

const SYMBOL_RE = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;

type Props = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function OptionsExposurePage({ searchParams }: Props) {
  const params = await searchParams;
  const rawSymbol = Array.isArray(params.symbol) ? params.symbol[0] : params.symbol;
  const symbol = rawSymbol && SYMBOL_RE.test(rawSymbol) ? rawSymbol.toUpperCase() : "MU";

  redirect(`/options/net-gex?symbol=${encodeURIComponent(symbol)}`);
}
