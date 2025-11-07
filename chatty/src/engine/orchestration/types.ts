// Shared types for Synth memory orchestration
// Aligns with VVAULT integration where STM (short-term memory) windows
// collaborate with LTM (vault) retrieval for prompt construction.

export type MemoryRole = 'user' | 'assistant' | 'system';

export interface STMContextEntry {
  id: string;
  role: MemoryRole;
  content: string;
  timestamp: number;
  sequence?: number;
}

export interface LTMContextEntry {
  id?: number;
  kind?: string;
  content: string;
  relevanceScore?: number;
  timestamp: number;
  metadata?: Record<string, any>;
}

export interface SummaryContextEntry {
  id: number;
  summaryType: string;
  content: string;
  startTs: number;
  endTs: number;
  createdAt: number;
}

export interface SynthMemoryContext {
  constructId: string;
  threadId: string;
  leaseToken?: string | null;
  stmWindow: STMContextEntry[];
  ltmEntries: LTMContextEntry[];
  summaries: SummaryContextEntry[];
  vaultStats?: {
    totalEntries: number;
    entriesByKind: Record<string, number>;
    totalSummaries: number;
    oldestEntry?: number;
    newestEntry?: number;
  };
  persona?: Record<string, unknown>;
  notes?: string[];
}

