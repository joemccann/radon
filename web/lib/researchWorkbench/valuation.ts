/** Scenario models, never price targets. All money inputs share the operator's unit. */
export interface ValuationInputs {
  ticker: string;
  cashFlow: number;
  growth: number;
  discountRate: number;
  terminalGrowth: number;
  years: number;
  netDebt: number;
  shares: number;
  ebitda: number;
  entryMultiple: number;
  exitMultiple: number;
  debtFraction: number;
  debtPaydown: number;
}
export interface ValuationResult {
  cashFlows: number[];
  presentValues: number[];
  terminalValue: number;
  enterpriseValue: number;
  equityValue: number;
  perShare: number;
  entryEnterpriseValue: number;
  sponsorEquity: number;
  exitDebt: number;
  exitEquity: number;
  moic: number;
  irr: number;
  compsEnterpriseValue: number;
  compsEquityValue: number;
}
export function valuationErrors(input: ValuationInputs): string[] {
  const errors: string[] = [];
  for (const [key, value] of Object.entries(input)) {
    if (key !== 'ticker' && (typeof value !== 'number' || !Number.isFinite(value))) errors.push(`${key} must be a finite number.`);
  }
  if (errors.length) return errors;
  if (!input.ticker.trim()) errors.push('Enter a ticker or company name.');
  if (!Number.isInteger(input.years) || input.years < 1 || input.years > 30) errors.push('Forecast years must be an integer from 1 to 30.');
  if (input.discountRate <= input.terminalGrowth) errors.push('Discount rate must exceed terminal growth.');
  if (input.discountRate <= 0 || input.discountRate > 1) errors.push('Discount rate must be greater than 0% and at most 100%.');
  if (input.growth <= -1 || input.growth > 1 || input.terminalGrowth <= -1) errors.push('Growth must exceed -100%; forecast growth must not exceed 100%.');
  if (input.shares <= 0) errors.push('Diluted shares must be positive.');
  if (input.ebitda <= 0) errors.push('Positive EBITDA is required for EV/EBITDA and this simplified LBO.');
  if (input.entryMultiple <= 0 || input.exitMultiple <= 0) errors.push('Entry and exit multiples must be positive.');
  if (input.debtFraction < 0 || input.debtFraction >= 1) errors.push('Acquisition debt must be from 0% to less than 100%.');
  if (input.debtPaydown < 0) errors.push('Annual debt paydown cannot be negative.');
  return errors;
}
export function calculateValuation(input: ValuationInputs): ValuationResult {
  const errors = valuationErrors(input);
  if (errors.length) throw new Error(errors.join(' '));
  const cashFlows = Array.from({length: input.years}, (_, i) => input.cashFlow * (1 + input.growth) ** (i + 1));
  const presentValues = cashFlows.map((cf, i) => cf / (1 + input.discountRate) ** (i + 1));
  const terminalValue = cashFlows.at(-1)! * (1 + input.terminalGrowth) / (input.discountRate - input.terminalGrowth);
  const enterpriseValue = presentValues.reduce((sum, v) => sum + v, 0) + terminalValue / (1 + input.discountRate) ** input.years;
  const equityValue = enterpriseValue - input.netDebt;
  const entryEnterpriseValue = input.ebitda * input.entryMultiple;
  const sponsorEquity = entryEnterpriseValue * (1 - input.debtFraction);
  const exitDebt = Math.max(0, entryEnterpriseValue * input.debtFraction - input.debtPaydown * input.years);
  // Limited-liability sponsor equity floors at zero. No interim distributions modeled.
  const exitEquity = Math.max(0, input.ebitda * (1 + input.growth) ** input.years * input.exitMultiple - exitDebt);
  const moic = exitEquity / sponsorEquity;
  const result = {cashFlows, presentValues, terminalValue, enterpriseValue, equityValue, perShare: equityValue / input.shares,
    entryEnterpriseValue, sponsorEquity, exitDebt, exitEquity, moic, irr: moic ** (1 / input.years) - 1,
    compsEnterpriseValue: input.ebitda * input.exitMultiple, compsEquityValue: input.ebitda * input.exitMultiple - input.netDebt};
  if (Object.values(result).flat().some(v => !Number.isFinite(v))) throw new Error('Scenario overflows numeric precision. Reduce the assumptions.');
  return result;
}

export interface ComparableCompany { name:string; enterpriseValue:number; ebitda:number }
export function comparableMultiples(peers:ComparableCompany[]):{name:string;multiple:number}[] {
  if(peers.length>30)throw new Error('Limit the peer set to 30 companies.');
  const seen=new Set<string>();
  return peers.map(peer=>{
    const name=peer.name.trim();
    if(!name||seen.has(name.toLowerCase()))throw new Error('Each comparable needs a unique company name.');
    seen.add(name.toLowerCase());
    if(!Number.isFinite(peer.enterpriseValue)||peer.enterpriseValue<=0||!Number.isFinite(peer.ebitda)||peer.ebitda<=0)throw new Error('Comparable EV and EBITDA must be positive finite values in the same units.');
    const multiple=peer.enterpriseValue/peer.ebitda;
    if(!Number.isFinite(multiple))throw new Error('Comparable multiple overflows numeric precision.');
    return {name,multiple};
  });
}
export function medianMultiple(peers:ComparableCompany[]):number|null {
  const values=comparableMultiples(peers).map(peer=>peer.multiple).sort((a,b)=>a-b);
  if(!values.length)return null;
  const middle=Math.floor(values.length/2);
  return values.length%2?values[middle]:(values[middle-1]+values[middle])/2;
}
