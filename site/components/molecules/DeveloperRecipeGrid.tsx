import { CopyAgentPrompt } from "@/components/molecules/CopyAgentPrompt";
import { developerRecipes } from "@/lib/agent-prompts";

export function DeveloperRecipeGrid() {
  return (
    <div className="mt-11 grid gap-4">
      {developerRecipes.map((recipe) => (
        <article
          key={recipe.id}
          className="rounded-[4px] border border-grid bg-figure-bg p-[22px]"
        >
          <h3 className="mb-4 font-serif text-[1.24rem] font-medium text-primary">
            {recipe.title}
          </h3>
          <CopyAgentPrompt prompt={recipe.prompt} />
        </article>
      ))}
    </div>
  );
}
