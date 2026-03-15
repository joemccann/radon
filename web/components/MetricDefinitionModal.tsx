"use client";

import Modal from "./Modal";

type Props = {
  open: boolean;
  title: string;
  value: string;
  definition: string;
  formula: string;
  onClose: () => void;
};

export default function MetricDefinitionModal({ open, title, value, definition, formula, onClose }: Props) {
  if (!open) return null;

  return (
    <Modal open onClose={onClose} title={title} className="metric-definition-modal">
      <div className="eb-total">
        <span className="eb-total-value neutral">{value}</span>
      </div>
      <div className="metric-definition-copy">
        <span className="metric-definition-label">What It Is</span>
        <p>{definition}</p>
      </div>
      <div className="metric-definition-copy">
        <span className="metric-definition-label">How It Is Calculated</span>
      </div>
      <div className="eb-formula">
        <code>{formula}</code>
      </div>
    </Modal>
  );
}
