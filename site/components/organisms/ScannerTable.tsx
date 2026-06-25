import { scannerRows } from "@/lib/editorial-content";

const toneClass: Record<string, string> = {
  accum: "text-signal-deep",
  dist: "text-negative",
  neutral: "text-secondary",
};

export function ScannerTable() {
  return (
    <table className="scan-table">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Read</th>
          <th className="text-right">Lead</th>
          <th className="text-right">Flow score</th>
        </tr>
      </thead>
      <tbody>
        {scannerRows.map((row) => (
          <tr key={row.ticker}>
            <td className="tk">{row.ticker}</td>
            <td className={toneClass[row.tone]}>{row.read}</td>
            <td className="num">{row.lead}</td>
            <td className="num">
              <span className="score-bar">
                <span className="track">
                  <span
                    className="fill"
                    style={{
                      width: `${row.score}%`,
                      background:
                        row.tone === "dist" ? "var(--color-negative)" : undefined,
                    }}
                  />
                </span>
                {row.score}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
