import { registryEntries } from "@/lib/editorial-content";

export function RegistryTable() {
  return (
    <div className="overflow-x-auto rounded-[4px] border border-grid">
      <table className="registry">
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Edge source</th>
            <th>Mechanism</th>
            <th>Structure</th>
            <th>Convexity</th>
            <th>Risk</th>
          </tr>
        </thead>
        <tbody>
          {registryEntries.map((entry) => (
            <tr key={entry.name}>
              <td className="name">
                {entry.name}
                <small>{entry.tag}</small>
              </td>
              <td>{entry.edge}</td>
              <td className="mech">{entry.mechanism}</td>
              <td>{entry.structure}</td>
              <td className="conv">{entry.convexity}</td>
              <td>
                <span className="risk-pill defined">{entry.risk}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
