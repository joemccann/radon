import { redirect } from "next/navigation";
import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/regime/llm");

/** Keep existing research bookmarks, including repeated filter values. */
export default async function RegimeLlmPage({ searchParams }: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(await searchParams)) {
    if (value === undefined) continue;
    for (const item of Array.isArray(value) ? value : [value]) query.append(key, item);
  }
  const suffix = query.toString();
  redirect(`/ai-industry${suffix ? `?${suffix}` : ""}`);
}
