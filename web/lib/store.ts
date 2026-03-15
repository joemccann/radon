import { create } from 'zustand';

interface AppState {
  activeSection: string;
  setActiveSection: (section: string) => void;
  theme: 'dark' | 'light' | null;
  setTheme: (theme: 'dark' | 'light' | null) => void;
  // Ticker Detail Modal State
  tickerDetail: {
    isOpen: boolean;
    ticker: string | null;
    positionId?: number;
  };
  openTicker: (ticker: string, positionId?: number) => void;
  closeTicker: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  activeSection: 'dashboard',
  setActiveSection: (section) => set({ activeSection: section }),
  theme: null,
  setTheme: (theme) => set({ theme }),
  
  tickerDetail: {
    isOpen: false,
    ticker: null,
  },
  openTicker: (ticker, positionId) => set({
    tickerDetail: { isOpen: true, ticker, positionId }
  }),
  closeTicker: () => set((state) => ({
    tickerDetail: { ...state.tickerDetail, isOpen: false }
  })),
}));