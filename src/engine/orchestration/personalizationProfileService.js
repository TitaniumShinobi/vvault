import schema from './chattyPersonalizationSchema.json';

const PROFILE_STORAGE_KEY = 'chattyPersonalizationProfile';

export const getSchema = () => schema;

export const buildDefaultProfile = (identityOverrides = {}, personalizationOverrides = {}) => {
  const now = new Date().toISOString();

  return {
    identity: {
      userId: 'pending-human',
      email: 'pending@vvault.local',
      vvaultLinked: true,
      provider: 'google',
      tier: 'free',
      ...identityOverrides
    },
    personalization: {
      enableCustomization: false,
      allowMemory: false,
      nickname: '',
      occupation: '',
      tags: [],
      aboutYou: '',
      ...personalizationOverrides
    },
    appearance: {
      theme: 'system',
      accentColor: 'Default',
      customTheme: {
        mode: 'light',
        name: 'Custom',
        tokens: {}
      },
      compactMode: false
    },
    language: {
      language: 'Auto-detect',
      spokenLanguage: 'Auto-detect'
    },
    voice: {
      voice: 'Maple'
    },
    aiPreferences: {
      model: 'gpt-4o-mini',
      openaiBaseUrl: 'https://api.openai.com/v1',
      showAdditionalModels: true,
      enableMemory: true,
      enableReasoning: true,
      enableFileProcessing: true,
      enableNarrativeSynthesis: true,
      enableLargeFileIntelligence: true,
      enableZenMode: true,
      reasoningDepth: 3,
      maxHistory: 100,
      maxFileSize: 10485760,
      chunkSize: 1000
    },
    notifications: {
      responsesPush: true,
      tasksPush: true,
      tasksEmail: true,
      recommendationsPush: true,
      recommendationsEmail: true
    },
    dataControls: {
      dataStorage: 'local',
      enableVVAULTMemory: false,
      memoryRetentionDays: 30,
      autoBackup: false,
      backupFrequency: 'weekly',
      dataExport: false,
      improveModel: false,
      remoteBrowserData: false
    },
    security: {
      twoFactorEnabled: false,
      sessionTimeout: 30,
      loginNotifications: true,
      suspiciousActivityAlerts: true
    },
    parentalControls: {
      enabled: false,
      contentFiltering: false,
      timeRestrictions: false,
      allowedHours: {
        start: '08:00',
        end: '22:00'
      }
    },
    account: {
      accountType: 'free',
      subscriptionStatus: 'active',
      subscriptionBenefits: [],
      gptBuilderProfile: {}
    },
    backup: {
      autoBackup: false,
      backupFrequency: 'weekly',
      cloudSync: false,
      localBackup: true
    },
    profilePicture: {
      source: 'google'
    },
    advanced: {
      showDebugPanel: false,
      showAdvancedFeatures: false,
      autoSave: true
    },
    metadata: {
      createdAt: now,
      lastUpdated: now,
      lastSeen: now,
      version: '1.0.0'
    },
    signals: {
      chattyTranscripts: [],
      harvestedSignals: [],
      publicRecords: [],
      signalVersion: '1.0.0'
    }
  };
};

export const mergeSignals = (profile, { chattyTranscripts = [], harvestedSignals = [], publicRecords = [] }) => {
  return {
    ...profile,
    signals: {
      chattyTranscripts: chattyTranscripts.length ? chattyTranscripts : profile.signals.chattyTranscripts,
      harvestedSignals: harvestedSignals.length ? harvestedSignals : profile.signals.harvestedSignals,
      publicRecords: publicRecords.length ? publicRecords : profile.signals.publicRecords,
      signalVersion: profile.signals.signalVersion || '1.0.0'
    },
    metadata: {
      ...profile.metadata,
      lastUpdated: new Date().toISOString()
    }
  };
};

export const persistProfile = (profile, storage = typeof localStorage !== 'undefined' ? localStorage : null) => {
  if (!storage) return profile;
  storage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profile));
  return profile;
};

export const loadProfile = (storage = typeof localStorage !== 'undefined' ? localStorage : null) => {
  if (!storage) return null;
  const raw = storage.getItem(PROFILE_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    console.error('Failed to parse stored profile', e);
    return null;
  }
};

export const exportProfileBlob = (profile) => {
  const blob = new Blob([JSON.stringify(profile, null, 2)], { type: 'application/json' });
  return {
    blob,
    fileName: 'chatty-personalization-profile.json'
  };
};

export const toVvaultCapsulePayload = (profile) => ({
  capsule_type: 'human_personalization_profile',
  created_at: profile.metadata?.createdAt,
  updated_at: profile.metadata?.lastUpdated,
  human: profile.identity,
  personalization: profile.personalization,
  appearance: profile.appearance,
  ai_preferences: profile.aiPreferences,
  signals: profile.signals
});

