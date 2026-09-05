import type { Metadata } from "next";
import { DeveloperRecipeGrid } from "@/components/molecules/DeveloperRecipeGrid";
import { AgentDocument } from "@/components/sections/AgentDocument";
import { recipesMetadata, recipesPage } from "@/lib/developer-pages";

export const metadata: Metadata = recipesMetadata;

export default function DeveloperRecipesPage() {
  return <AgentDocument page={recipesPage} afterIntro={<DeveloperRecipeGrid />} />;
}
