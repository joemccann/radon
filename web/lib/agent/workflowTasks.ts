/**
 * Workflow tasks — maps a pipeline graph + its run report onto TaskRuns.
 *
 * The executor already returns a per-node RunReport (rows in/out, which node
 * blocked, which gate failed). That is the live run state TaskRuns wants; the
 * Workflow surface previously threw it away into a one-line status string.
 *
 * Two entry points because a run has two observable states:
 *   - in flight  → graphToRunningTasks: nodes from the graph, first one running
 *   - settled    → runReportToTasks: real per-node row counts and the block point
 */

import type { AgentTask, SubStep, TaskState } from "@/components/agent";
import type { RunReport, RunStep, WorkflowGraph } from "@/app/workflow/workflowClient";

function nodeTitle(nodeType: string, nodeId: string): string {
  const label = nodeType.replace(/_/g, " ");
  const titled = label.charAt(0).toUpperCase() + label.slice(1);
  return `${titled} · ${nodeId}`;
}

function stepSubSteps(step: RunStep): SubStep[] {
  const subs: SubStep[] = [
    { label: "rows in", meta: String(step.rows_in) },
    { label: "rows out", meta: String(step.rows_out) },
  ];

  const gate = step.info?.gate;
  if (typeof gate === "string" && gate) {
    subs.push({ label: "gate", meta: gate.toUpperCase() });
  }
  return subs;
}

/**
 * Per-node tasks for a settled run. Everything up to the blocking node ran, so
 * it reads `done`; the blocking node itself is `queued` — it halted rather than
 * completing, and TaskRuns has no failure state by design (the block reason is
 * carried in the sub-steps and the surface's own status strip).
 */
export function runReportToTasks(report: RunReport): AgentTask[] {
  return report.steps.map((step) => {
    const state: TaskState = step.blocked ? "queued" : "done";
    return {
      id: step.node_id,
      title: nodeTitle(step.node_type, step.node_id),
      meta: `${step.rows_out} ROWS`,
      state,
      steps: stepSubSteps(step),
    };
  });
}

/**
 * Per-node tasks while a run is in flight. The executor returns the whole
 * report at once rather than streaming, so the first node shows `running` and
 * the rest `queued` — an honest "started, not yet reported" rather than a fake
 * progress animation across every node.
 */
export function graphToRunningTasks(graph: WorkflowGraph): AgentTask[] {
  return graph.nodes.map((node, index) => ({
    id: node.id,
    title: nodeTitle(node.type, node.id),
    state: index === 0 ? "running" : "queued",
  }));
}
