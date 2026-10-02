import { runAssistantLoop, type AssistantTurn } from "../../lib/assistant/loop";

// Runs under tsx so the same alias and server-module resolution as the app
// applies. The browser test points the provider at its loopback fixture only.
async function main() {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const result = await runAssistantLoop(
    JSON.parse(input) as AssistantTurn[],
    "Answer the user's options structure question.",
    { userId: "browser-fixture", kind: "test" },
    { model: "grok-4.7", provider: "xai" },
  );
  console.log("assistant-result:" + JSON.stringify({ content: result.content }));
}

main().catch(error => { console.error(error); process.exitCode = 1; });
