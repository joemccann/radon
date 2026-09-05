import { CopyAgentPrompt } from "@/components/molecules/CopyAgentPrompt";
import { getCapabilityPrompt } from "@/lib/agent-prompts";

interface Props {
  capabilityId: string;
  className?: string;
}

export function CopyAgentPromptBar({
  capabilityId,
  className = "mt-7",
}: Props) {
  return (
    <div className={className}>
      <CopyAgentPrompt prompt={getCapabilityPrompt(capabilityId)} />
    </div>
  );
}
