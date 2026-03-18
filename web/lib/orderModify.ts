export type ModifyComboLeg = {
  action: "BUY" | "SELL";
  expiry: string;
  strike: number;
  right: "C" | "P";
  ratio: number;
  limitPrice?: number;
};

export type ReplaceComboOrder = {
  type: "combo";
  symbol: string;
  action: "BUY" | "SELL";
  quantity: number;
  limitPrice: number;
  tif?: "DAY" | "GTC";
  legs: ModifyComboLeg[];
};

export type ModifyOrderRequest = {
  newPrice?: number;
  newQuantity?: number;
  outsideRth?: boolean;
  replaceOrder?: ReplaceComboOrder;
};
