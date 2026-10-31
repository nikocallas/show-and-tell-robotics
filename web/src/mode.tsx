/** Demo/live mode as React context (see config.ts for how the initial mode is chosen). */

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { resolveInitialMode, storeMode, type Mode } from "./config";

interface ModeValue {
  mode: Mode;
  setMode: (mode: Mode) => void;
}

const ModeContext = createContext<ModeValue>({ mode: "demo", setMode: () => undefined });

export function ModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<Mode>(resolveInitialMode);
  const value = useMemo<ModeValue>(
    () => ({
      mode,
      setMode: (next) => {
        storeMode(next);
        setModeState(next);
      },
    }),
    [mode],
  );
  return <ModeContext.Provider value={value}>{children}</ModeContext.Provider>;
}

export function useMode(): ModeValue {
  return useContext(ModeContext);
}
