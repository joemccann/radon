import { routeMetadata } from "@/lib/pageTitle";

export const metadata = routeMetadata("/kit");

export default function KitLayout({ children }: { children: React.ReactNode }) {
  return children;
}
