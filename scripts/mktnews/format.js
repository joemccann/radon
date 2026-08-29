export function shouldEmit(msg, { all = false } = {}) {
  if (!msg) return false;
  if (msg.kind === "time" && !all) return false;
  return true;
}

function flashText(data) {
  if (!data || typeof data !== "object") return "";
  const inner = data.data && typeof data.data === "object" ? data.data : data;
  return String(inner.content || inner.title || "").replace(/\s+/g, " ").trim();
}

function impactSuffix(data) {
  const impacts = Array.isArray(data?.impact) ? data.impact : [];
  if (!impacts.length) return "";
  return (
    "  " +
    impacts
      .map((row) => {
        const symbol = row?.symbol || "";
        const impact = row?.impact || "";
        return [symbol, impact].filter(Boolean).join(":");
      })
      .filter(Boolean)
      .join(" ")
  );
}

export function formatMessage(msg, { pretty = false, all = false } = {}) {
  if (!shouldEmit(msg, { all })) return null;
  if (!pretty) return JSON.stringify({ kind: msg.kind, type: msg.type, data: msg.data });
  if (msg.kind === "time") {
    return `TIME  ${new Date(msg.data).toISOString()}`;
  }
  if (msg.kind === "flash") {
    const when = msg.data?.time || "";
    const important = msg.data?.important ? " IMPORTANT" : "";
    return `FLASH${important}  ${when}  ${flashText(msg.data)}${impactSuffix(msg.data)}`.trim();
  }
  if (msg.kind === "news") {
    const title = msg.data?.title || flashText(msg.data);
    return `NEWS  ${title}`.trim();
  }
  if (msg.kind === "raw") return `RAW  ${msg.raw}`;
  return `${String(msg.kind).toUpperCase()}  ${JSON.stringify(msg.data)}`;
}
