"use client";

/**
 * TaskRuns — live agent task status with nested sub-step telemetry. Adopted
 * from beautifului.dev "Task Rows". Replaces the static Workflow job list.
 */

export type TaskState = "done" | "running" | "queued";

export type SubStep = { label: string; meta: string };

export type AgentTask = {
  id: string;
  title: string;
  /** Mono count meta, e.g. "34 INSTR", "4 BOOKS". */
  meta?: string;
  state: TaskState;
  steps?: SubStep[];
};

const STATE_LABEL: Record<TaskState, string> = {
  done: "COMPLETED",
  running: "RUNNING",
  queued: "QUEUED",
};

export default function TaskRuns({ tasks }: { tasks: AgentTask[] }) {
  const active = tasks.filter((t) => t.state === "running").length;
  return (
    <section className="task-runs" aria-label="Agent runs">
      <div className="task-runs__head">
        <span className="task-runs__module">WORKFLOW</span>
        <span className="task-runs__title">Agent runs</span>
        <span className="task-runs__status">
          {active > 0 ? (
            <>
              <span className="agent-dot agent-dot--warn agent-dot--pulse" aria-hidden="true" />
              {active} ACTIVE
            </>
          ) : (
            "IDLE"
          )}
        </span>
      </div>
      <ol className="task-runs__list">
        {tasks.map((task, i) => (
          <li key={task.id} className="task-runs__task" data-state={task.state}>
            <div className="task-runs__row">
              <span className="task-runs__index">{i + 1}</span>
              <span
                className={`agent-dot${
                  task.state === "running"
                    ? " agent-dot--warn agent-dot--pulse"
                    : task.state === "done"
                      ? " agent-dot--signal"
                      : " agent-dot--muted"
                }`}
                aria-hidden="true"
              />
              <span className="task-runs__task-title">{task.title}</span>
              {task.meta ? <span className="task-runs__task-meta">{task.meta}</span> : null}
              <span
                className={`agent-chip${
                  task.state === "done"
                    ? " agent-chip--signal"
                    : task.state === "running"
                      ? " agent-chip--warn"
                      : ""
                }`}
              >
                {STATE_LABEL[task.state]}
              </span>
            </div>
            {task.steps?.length ? (
              <ul className="task-runs__substeps">
                {task.steps.map((s) => (
                  <li key={s.label} className="task-runs__substep">
                    <span>{s.label}</span>
                    <span className="task-runs__substep-meta">{s.meta}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}
