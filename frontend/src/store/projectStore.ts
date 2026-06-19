/**
 * src/store/projectStore.ts
 *
 * App-wide active-project context. Kept tiny and standalone (persisted to
 * localStorage) so the project switcher in the TopBar is the single source of
 * truth for "which project am I looking at" across the shell.
 */

import { create } from 'zustand';

const STORAGE_KEY = 'aipom.activeProjectId';

interface ProjectState {
  activeProjectId: string | null;
  setActiveProjectId: (id: string | null) => void;
}

export const useProjectStore = create<ProjectState>((set) => ({
  activeProjectId:
    typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null,
  setActiveProjectId: (id) => {
    if (typeof localStorage !== 'undefined') {
      if (id) localStorage.setItem(STORAGE_KEY, id);
      else localStorage.removeItem(STORAGE_KEY);
    }
    set({ activeProjectId: id });
  },
}));
