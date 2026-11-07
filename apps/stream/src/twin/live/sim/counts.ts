import { create } from "zustand";

/** How many field-driven people and vehicles are out right now; written by the movers about once a second. */
export const useTwinCounts = create<{ people: number; vehicles: number }>(() => ({ people: 0, vehicles: 0 }));
