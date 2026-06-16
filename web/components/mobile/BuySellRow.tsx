type BuySellRowProps = {
  side: "BUY" | "SELL";
  label: string;
  price: string;
  sub?: string;
};

export function BuySellRow({ side, label, price, sub }: BuySellRowProps) {
  const sideClass = side === "BUY" ? "m-leg--buy" : "m-leg--sell";

  return (
    <div className={`m-leg ${sideClass}`}>
      <span className="m-leg__direction">{side}</span>
      <span className="m-leg__price">{price}</span>
      {label ? <span className="m-leg__sub">{label}</span> : null}
      {sub ? <span className="m-leg__sub">{sub}</span> : null}
    </div>
  );
}

export default BuySellRow;
