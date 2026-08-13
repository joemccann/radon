"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  describeRunReport,
  allocateWorkflowNodeId,
  listWorkflows,
  runWorkflowAfterConfirmation,
  runWorkflow,
  saveWorkflow,
  toExecutorGraph,
  type RunReport,
  type SavedWorkflow,
  WORKFLOW_PALETTE,
} from "./workflowClient";

// F14 — minimal working flow-pipeline canvas. Palette adds nodes; edges connect
// them; Save persists to Turso; Run executes server-side (Python executor). An
// order node forces the OrderRiskGate confirmation before a run can place it.

type NodeData = { label: string; nodeType: string; params: Record<string, unknown> };

function Canvas() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [name, setName] = useState("Untitled pipeline");
  const [graphId, setGraphId] = useState<string | undefined>(undefined);
  const [saved, setSaved] = useState<SavedWorkflow[]>([]);
  const [status, setStatus] = useState<string>("");
  const [report, setReport] = useState<RunReport | null>(null);

  useEffect(() => {
    listWorkflows()
      .then(setSaved)
      .catch(() => setSaved([]));
  }, []);

  const onConnect = useCallback(
    (connection: Connection) => setEdges((eds) => addEdge(connection, eds)),
    [setEdges],
  );

  const addNode = useCallback(
    (entry: (typeof WORKFLOW_PALETTE)[number]) => {
      setNodes((nds) => {
        const id = allocateWorkflowNodeId(nds.map((node) => node.id));
        return [...nds, {
          id,
          position: { x: 80 + nds.length * 40, y: 60 + nds.length * 70 },
          data: { label: entry.label, nodeType: entry.type, params: entry.params },
          style: nodeStyle(entry.type),
        } as Node<NodeData>];
      });
    },
    [setNodes],
  );

  const currentGraph = useCallback(() => {
    const executorNodes = nodes.map((n) => ({
      id: n.id,
      type: n.data.nodeType,
      params: n.data.params,
      position: n.position,
    }));
    const executorEdges = edges
      .filter((e) => e.source && e.target)
      .map((e) => ({ from: e.source, to: e.target }));
    return toExecutorGraph(executorNodes, executorEdges);
  }, [nodes, edges]);

  const onSave = useCallback(async () => {
    try {
      setStatus("Saving...");
      const id = await saveWorkflow(name, currentGraph(), graphId);
      setGraphId(id);
      setStatus("Saved");
      setSaved(await listWorkflows());
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Save failed");
    }
  }, [name, currentGraph, graphId]);

  const onRun = useCallback(async () => {
    const graph = currentGraph();
    try {
      const result = await runWorkflowAfterConfirmation(graph, window.confirm, runWorkflow);
      if (!result) {
        setStatus("Run cancelled");
        return;
      }
      setStatus("Running...");
      setReport(result);
      setStatus(describeRunReport(result));
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Run failed");
    }
  }, [currentGraph]);

  const onLoad = useCallback(
    (entry: SavedWorkflow) => {
      setName(entry.name);
      setGraphId(entry.id);
      setReport(null);
      const loadedNodes = entry.graph.nodes.map((n, i) => ({
        id: n.id,
        position: n.position ?? { x: 80 + i * 40, y: 60 + i * 70 },
        data: { label: paletteLabel(n.type, n.params), nodeType: n.type, params: n.params },
        style: nodeStyle(n.type),
      })) as Node<NodeData>[];
      const loadedEdges = entry.graph.edges.map((e, i) => ({
        id: `e${i}`,
        source: e.from,
        target: e.to,
      })) as Edge[];
      setNodes(loadedNodes);
      setEdges(loadedEdges);
      setStatus(`Loaded "${entry.name}"`);
    },
    [setNodes, setEdges],
  );

  return (
    <div style={pageStyle}>
      <header style={headerStyle}>
        <h1 style={titleStyle}>Flow Pipeline Composer</h1>
        <input
          aria-label="Pipeline name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={nameInputStyle}
        />
        <button type="button" onClick={onSave} style={btnStyle}>
          Save
        </button>
        <button type="button" onClick={onRun} style={btnPrimaryStyle}>
          Run
        </button>
        <span style={statusStyle}>{status}</span>
      </header>

      <div style={bodyStyle}>
        <aside style={paletteStyle}>
          <div style={paletteHeadStyle}>Nodes</div>
          {WORKFLOW_PALETTE.map((entry) => (
            <button key={entry.label} type="button" onClick={() => addNode(entry)} style={paletteBtnStyle}>
              {entry.label}
            </button>
          ))}

          <div style={paletteHeadStyle}>Saved</div>
          {saved.length === 0 ? (
            <div style={emptyStyle}>No saved pipelines</div>
          ) : (
            saved.map((entry) => (
              <button key={entry.id} type="button" onClick={() => onLoad(entry)} style={paletteBtnStyle}>
                {entry.name}
              </button>
            ))
          )}
        </aside>

        <div style={canvasStyle}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            fitView
          >
            <Background />
            <Controls />
          </ReactFlow>
        </div>
      </div>

      {report ? <RunReportStrip report={report} /> : null}
    </div>
  );
}

function RunReportStrip({ report }: { report: RunReport }) {
  return (
    <div style={reportStyle}>
      <strong style={{ color: report.ok ? "var(--positive)" : "var(--negative)" }}>
        {report.ok ? "OK" : "BLOCKED"}
      </strong>
      <span style={{ marginLeft: 12 }}>{describeRunReport(report)}</span>
      <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {report.steps.map((step) => (
          <span key={step.node_id} style={stepChipStyle(step.blocked)}>
            {step.node_type} ({step.rows_in} to {step.rows_out})
          </span>
        ))}
      </div>
    </div>
  );
}

function paletteLabel(type: string, params: Record<string, unknown>): string {
  const match = WORKFLOW_PALETTE.find((p) => p.type === type && JSON.stringify(p.params) === JSON.stringify(params));
  return match?.label ?? type;
}

function nodeStyle(type: string): React.CSSProperties {
  const accent: Record<string, string> = {
    "data-source": "var(--signal-core)",
    filter: "var(--info)",
    gate: "var(--extreme)",
    notify: "var(--positive)",
    order: "var(--negative)",
  };
  return {
    background: "var(--bg-panel-raised)",
    color: "var(--text-primary)",
    border: `1px solid ${accent[type] ?? "var(--border)"}`,
    borderRadius: 4,
    padding: "8px 12px",
    fontSize: 12,
    fontFamily: "var(--font-mono)",
  };
}

const pageStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100vh",
  background: "var(--bg-base)",
  color: "var(--text-primary)",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "12px 16px",
  borderBottom: "1px solid var(--border)",
  background: "var(--bg-panel)",
};

const titleStyle: React.CSSProperties = { fontSize: 14, fontWeight: 600, margin: 0, marginRight: 8 };

const nameInputStyle: React.CSSProperties = {
  background: "var(--bg-elevated)",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "4px 8px",
  fontSize: 12,
};

const btnStyle: React.CSSProperties = {
  background: "var(--bg-elevated)",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "4px 12px",
  fontSize: 12,
  cursor: "pointer",
};

const btnPrimaryStyle: React.CSSProperties = {
  ...btnStyle,
  background: "var(--accent-bg)",
  color: "var(--text-on-accent)",
  borderColor: "var(--accent-bg)",
};

const statusStyle: React.CSSProperties = { marginLeft: "auto", fontSize: 12, color: "var(--text-secondary)" };

const bodyStyle: React.CSSProperties = { display: "flex", flex: 1, minHeight: 0 };

const paletteStyle: React.CSSProperties = {
  width: 220,
  borderRight: "1px solid var(--border)",
  padding: 12,
  display: "flex",
  flexDirection: "column",
  gap: 6,
  overflowY: "auto",
  background: "var(--bg-panel)",
};

const paletteHeadStyle: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  color: "var(--text-muted)",
  marginTop: 8,
};

const paletteBtnStyle: React.CSSProperties = {
  ...btnStyle,
  textAlign: "left",
  width: "100%",
};

const emptyStyle: React.CSSProperties = { fontSize: 11, color: "var(--text-muted)" };

const canvasStyle: React.CSSProperties = { flex: 1, minWidth: 0 };

const reportStyle: React.CSSProperties = {
  borderTop: "1px solid var(--border)",
  padding: "12px 16px",
  background: "var(--bg-panel)",
  fontSize: 12,
};

function stepChipStyle(blocked: boolean): React.CSSProperties {
  return {
    border: `1px solid ${blocked ? "var(--negative)" : "var(--border)"}`,
    borderRadius: 4,
    padding: "2px 8px",
    fontFamily: "var(--font-mono)",
    color: blocked ? "var(--negative)" : "var(--text-secondary)",
  };
}

export default function WorkflowComposer() {
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  );
}
