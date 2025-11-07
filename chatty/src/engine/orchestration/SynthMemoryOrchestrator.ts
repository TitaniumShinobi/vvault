import { constructRegistry } from '../../state/constructs';
import { threadManager } from '../../core/thread/SingletonThreadManager';
import { stmBuffer, type MessagePacket } from '../../core/memory/STMBuffer';
import type { VaultStore } from '../../core/vault/VaultStore';
import type {
  SynthMemoryContext,
  STMContextEntry,
  LTMContextEntry,
  SummaryContextEntry,
  MemoryRole
} from './types';

type PersonaProvider = (userId: string) => Record<string, unknown>;

type VVAULTConnector = {
  writeTranscript?: (params: {
    userId: string;
    sessionId: string;
    timestamp: string;
    role: string;
    content: string;
    filename?: string;
    emotionScores?: Record<string, number>;
    metadata?: Record<string, unknown>;
  }) => Promise<unknown>;
  readMemories?: (userId: string, options?: Record<string, unknown>) => Promise<Array<{
    timestamp: string;
    role: string;
    content: string;
    filename?: string;
    metadata?: Record<string, unknown>;
  }>>;
};

export interface SynthMemoryOrchestratorOptions {
  constructId?: string;
  userId?: string;
  threadId?: string;
  leaseDurationMs?: number;
  maxStmWindow?: number;
  maxLtmEntries?: number;
  maxSummaries?: number;
  personaProvider?: PersonaProvider;
  vvaultConnector?: VVAULTConnector;
}

const DEFAULT_ROLE_LOCK = {
  allowedRoles: ['assistant', 'analyst', 'companion'],
  prohibitedRoles: [],
  contextBoundaries: ['general', 'technical', 'creative'],
  behaviorConstraints: [
    'maintain conversational tone',
    'adhere to developer safety policies'
  ]
};

const DEFAULT_CONSTRUCT_DESCRIPTION =
  'Default construct for Chatty synthesizer sessions. Maintains STM/LTM continuity via VVAULT.';

export class SynthMemoryOrchestrator {
  private readonly constructId: string;
  private readonly userId: string;
  private threadId: string | null;
  private leaseToken: string | null = null;
  private vaultStore: VaultStore | null = null;
  private readonly personaProvider?: PersonaProvider;
  private readonly vvaultConnector?: VVAULTConnector;
  private readonly maxStmWindow: number;
  private readonly maxLtmEntries: number;
  private readonly maxSummaries: number;
  private readonly leaseDurationMs: number;
  private initializationPromise: Promise<void> | null = null;
  private availabilityNotes: string[] = [];

  constructor(options: SynthMemoryOrchestratorOptions = {}) {
    this.constructId = options.constructId ?? 'default-construct';
    this.userId = options.userId ?? 'cli';
    this.threadId = options.threadId ?? null;
    this.personaProvider = options.personaProvider;
    this.vvaultConnector = options.vvaultConnector;
    this.maxStmWindow = options.maxStmWindow ?? 100;
    this.maxLtmEntries = options.maxLtmEntries ?? 32;
    this.maxSummaries = options.maxSummaries ?? 4;
    this.leaseDurationMs = options.leaseDurationMs ?? 5 * 60 * 1000;
  }

  /**
   * Ensure construct, vault store, and thread are ready before use.
   */
  async ensureReady(): Promise<void> {
    if (!this.initializationPromise) {
      this.initializationPromise = this.initialize();
    }
    await this.initializationPromise;
  }

  /**
   * Record a user/assistant message into STM, LTM, and VVAULT (if available).
   */
  async captureMessage(
    role: MemoryRole,
    content: string,
    metadata: Record<string, unknown> = {}
  ): Promise<void> {
    await this.ensureReady();
    const threadId = this.threadId!;
    const timestamp = Date.now();
    const messageId = this.generateMessageId(role);
    const packet: MessagePacket = {
      id: messageId,
      role,
      content,
      timestamp,
      metadata
    };

    try {
      stmBuffer.addMessage(this.constructId, threadId, packet);
    } catch (error) {
      this.logWarning('Failed to append message to STM buffer', error);
    }

    if (this.vaultStore) {
      try {
        await this.vaultStore.saveMessage(threadId, {
          role,
          content,
          timestamp,
          metadata: {
            ...metadata,
            orchestrator: 'synth-memory',
            source: metadata.source ?? 'cli-runtime'
          }
        });
      } catch (error) {
        this.logWarning('Failed to persist message to vault store', error);
      }
    } else {
      this.noteIfMissing('Vault store unavailable - using VVAULT transcripts only');
    }

    if (this.vvaultConnector?.writeTranscript) {
      await this.writeTranscriptToVvault(role, content, timestamp, metadata).catch(error =>
        this.logWarning('Failed to write transcript to VVAULT', error)
      );
    }
  }

  /**
   * Build orchestrated memory context for prompt construction.
   */
  async prepareMemoryContext(
    overrides: Partial<Pick<SynthMemoryOrchestratorOptions, 'maxStmWindow' | 'maxLtmEntries' | 'maxSummaries'>> = {}
  ): Promise<SynthMemoryContext> {
    await this.ensureReady();

    const stmLimit = overrides.maxStmWindow ?? this.maxStmWindow;
    const ltmLimit = overrides.maxLtmEntries ?? this.maxLtmEntries;
    const summaryLimit = overrides.maxSummaries ?? this.maxSummaries;

    const [stmWindow, ltmEntries, summaries, stats] = await Promise.all([
      this.loadSTMWindow(stmLimit),
      this.loadLTMEntries(ltmLimit),
      this.loadSummaries(summaryLimit),
      this.loadVaultStats()
    ]);

    const persona = this.personaProvider ? this.personaProvider(this.userId) : undefined;
    const context: SynthMemoryContext = {
      constructId: this.constructId,
      threadId: this.threadId!,
      leaseToken: this.leaseToken,
      stmWindow,
      ltmEntries,
      summaries
    };

    if (stats) {
      context.vaultStats = stats;
    }
    if (persona) {
      context.persona = persona;
    }
    if (this.availabilityNotes.length > 0) {
      context.notes = [...new Set(this.availabilityNotes)];
    }

    return context;
  }

  private async initialize(): Promise<void> {
    await this.ensureConstruct();
    await this.ensureThread();
  }

  /**
   * Ensure construct exists and hydrate vault store.
   */
  private async ensureConstruct(): Promise<void> {
    const existing = await constructRegistry.getConstruct(this.constructId);
    if (existing) {
      this.vaultStore = existing.vaultStore;
      return;
    }

    const fingerprint = `fingerprint_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    await constructRegistry.registerConstruct({
      id: this.constructId,
      name: 'Chatty Synth Construct',
      description: DEFAULT_CONSTRUCT_DESCRIPTION,
      roleLock: DEFAULT_ROLE_LOCK,
      legalDocSha256: 'chatty-synth-default-legal-sha256',
      vaultPointer: `vvault/users/${this.userId}`,
      fingerprint
    });

    const created = await constructRegistry.getConstruct(this.constructId);
    this.vaultStore = created?.vaultStore ?? null;
    if (!this.vaultStore) {
      this.noteIfMissing('Vault store unavailable after construct registration');
    }
  }

  /**
   * Ensure a thread and lease are available.
   */
  private async ensureThread(): Promise<void> {
    if (this.threadId) {
      return;
    }

    const threads = await threadManager.getThreads(this.constructId);
    let targetThreadId: string;

    if (threads.length > 0) {
      targetThreadId = threads[0].id;
    } else {
      const created = await threadManager.createThread(
        this.constructId,
        'Synthesizer Session'
      );
      targetThreadId = created.id;
    }

    try {
      this.leaseToken = await threadManager.acquireLease(
        this.constructId,
        targetThreadId,
        this.leaseDurationMs
      );
    } catch (error) {
      this.logWarning('Failed to acquire thread lease', error);
      this.noteIfMissing('Thread lease acquisition failed - proceeding without lease');
    }

    this.threadId = targetThreadId;
  }

  private async loadSTMWindow(limit: number): Promise<STMContextEntry[]> {
    try {
      const window = await stmBuffer.getWindow(this.constructId, this.threadId!, limit);
      return window.map(msg => ({
        id: msg.id,
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp,
        sequence: (msg as any).sequence
      }));
    } catch (error) {
      this.logWarning('Failed to load STM window', error);
      this.noteIfMissing('STM buffer unavailable - memory window is empty');
      return [];
    }
  }

  private async loadLTMEntries(limit: number): Promise<LTMContextEntry[]> {
    if (this.vaultStore) {
      try {
        const results = await this.vaultStore.search({
          constructId: this.constructId,
          threadId: this.threadId ?? undefined,
          kind: 'LTM',
          limit,
          minRelevanceScore: 0
        });

        if (results.length === 0 && this.vvaultConnector?.readMemories) {
          // Fall back to VVAULT transcripts if vault has not yet been populated
          return this.loadMemoriesFromVvault(limit);
        }

        return results.map(entry => ({
          id: entry.id,
          kind: entry.kind,
          content: typeof entry.payload === 'string' ? entry.payload : JSON.stringify(entry.payload),
          relevanceScore: entry.relevanceScore ?? undefined,
          timestamp: entry.timestamp,
          metadata: entry.metadata
        }));
      } catch (error) {
        this.logWarning('Failed to load LTM entries from vault', error);
        this.noteIfMissing('Vault search failed - using VVAULT transcripts');
      }
    }

    if (this.vvaultConnector?.readMemories) {
      return this.loadMemoriesFromVvault(limit);
    }

    this.noteIfMissing('No LTM source available');
    return [];
  }

  private async loadSummaries(limit: number): Promise<SummaryContextEntry[]> {
    if (!this.vaultStore) {
      return [];
    }

    try {
      const summaries = await this.vaultStore.getVaultSummaryMeta();
      return summaries.slice(0, limit);
    } catch (error) {
      this.logWarning('Failed to load vault summaries', error);
      return [];
    }
  }

  private async loadVaultStats(): Promise<SynthMemoryContext['vaultStats'] | undefined> {
    if (!this.vaultStore) {
      return undefined;
    }

    try {
      return await this.vaultStore.getStats();
    } catch (error) {
      this.logWarning('Failed to load vault statistics', error);
      return undefined;
    }
  }

  private async loadMemoriesFromVvault(limit: number): Promise<LTMContextEntry[]> {
    if (!this.vvaultConnector?.readMemories) {
      return [];
    }

    try {
      const memories = await this.vvaultConnector.readMemories(this.userId, {
        limit,
        sessionId: this.threadId ?? undefined
      });

      return memories.slice(0, limit).map(memory => ({
        id: memory.filename ? this.stripExtension(memory.filename) : undefined,
        kind: 'TRANSCRIPT',
        content: memory.content,
        timestamp: new Date(memory.timestamp).getTime(),
        metadata: {
          source: 'vvault',
          role: memory.role,
          filename: memory.filename,
          ...(memory.metadata ?? {})
        }
      }));
    } catch (error) {
      this.logWarning('Failed to read memories from VVAULT', error);
      return [];
    }
  }

  private async writeTranscriptToVvault(
    role: MemoryRole,
    content: string,
    timestamp: number,
    metadata: Record<string, unknown>
  ): Promise<void> {
    if (!this.vvaultConnector?.writeTranscript) {
      return;
    }

    await this.vvaultConnector.writeTranscript({
      userId: this.userId,
      sessionId: this.threadId ?? 'default-session',
      timestamp: new Date(timestamp).toISOString(),
      role,
      content,
      metadata
    });
  }

  private generateMessageId(role: MemoryRole): string {
    try {
      const maybeCrypto = (globalThis as unknown as { crypto?: { randomUUID?: () => string } }).crypto;
      if (maybeCrypto?.randomUUID) {
        return `${role}_${maybeCrypto.randomUUID()}`;
      }
    } catch {
      // Silent fallback
    }
    return `${role}_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  }

  private stripExtension(value?: string): string | undefined {
    if (!value) return value;
    const index = value.lastIndexOf('.');
    return index === -1 ? value : value.slice(0, index);
  }

  private logWarning(message: string, error: unknown): void {
    if (typeof console !== 'undefined') {
      console.warn(`[SynthMemoryOrchestrator] ${message}`, error);
    }
  }

  private noteIfMissing(note: string): void {
    this.availabilityNotes.push(note);
  }
}

