'use client';

import React, { createContext, useCallback, useContext, useMemo } from 'react';

type SidebarLayoutValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
};

const SidebarLayoutContext = createContext<SidebarLayoutValue | null>(null);

export function SidebarLayoutProvider({
  open,
  setOpen,
  children,
}: {
  open: boolean;
  setOpen: (open: boolean) => void;
  children: React.ReactNode;
}) {
  const toggle = useCallback(() => setOpen(!open), [open, setOpen]);
  const value = useMemo(
    () => ({ open, setOpen, toggle }),
    [open, setOpen, toggle],
  );
  return (
    <SidebarLayoutContext.Provider value={value}>
      {children}
    </SidebarLayoutContext.Provider>
  );
}

export function useSidebarLayout(): SidebarLayoutValue | null {
  return useContext(SidebarLayoutContext);
}
