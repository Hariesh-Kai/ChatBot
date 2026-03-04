"use client";

import { createContext, useContext } from "react";

export interface WorkspaceSelectionContextValue {
  activeProjectId: string | null;
  activeRevision: string | null;
  setActiveProjectId: (value: string | null) => void;
  setActiveRevision: (value: string | null) => void;
}

const WorkspaceSelectionContext = createContext<WorkspaceSelectionContextValue | null>(null);

export function WorkspaceSelectionProvider({
  value,
  children,
}: {
  value: WorkspaceSelectionContextValue;
  children: React.ReactNode;
}) {
  return (
    <WorkspaceSelectionContext.Provider value={value}>
      {children}
    </WorkspaceSelectionContext.Provider>
  );
}

export function useWorkspaceSelection() {
  const ctx = useContext(WorkspaceSelectionContext);
  if (!ctx) {
    throw new Error("useWorkspaceSelection must be used within WorkspaceSelectionProvider");
  }
  return ctx;
}

