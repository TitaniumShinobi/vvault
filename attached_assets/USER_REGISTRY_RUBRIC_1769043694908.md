# Chatty User Registry Architecture Rubric
## Industry Standards + Construct-Aware Identity Management

### 🎯 **Core Problem Identified**
Chatty currently has **NO persistent user identity system**, causing:
- ❌ **No user-level segregation** in VVAULT or memory
- ❌ **Conversations detached** from persistent identity  
- ❌ **Cannot build stateful constructs** (tone, memory, singleton enforcement)
- ❌ **VVAULT paths meaningless** without anchored identity
- ❌ **Memory writes unnamespaced** - data bleeding between users

---

## 📋 **Industry Standards Rubric**

### **Tier 1: Basic User Registry (Minimum Viable)**
| Requirement | Status | Implementation |
|-------------|--------|----------------|
| ✅ **Persistent Email Storage** | ❌ Missing | MongoDB `email` field, unique index |
| ✅ **User Status Tracking** | ❌ Missing | `status: 'active'\|'deleted'\|'suspended'` |
| ✅ **Soft Delete Support** | ❌ Missing | `deletedAt` timestamp, prevent re-registration |
| ✅ **Audit Trail** | ❌ Missing | `createdAt`, `lastLoginAt`, `deletedAt` |
| ✅ **Session Identity** | ❌ Missing | JWT with persistent `sub` field |

### **Tier 2: Construct-Aware Identity (Advanced)**
| Requirement | Status | Implementation |
|-------------|--------|----------------|
| ✅ **Construct ID Mapping** | ❌ Missing | `constructId: 'HUMAN-DEVON'` |
| ✅ **VVAULT Path Anchoring** | ❌ Missing | `/vvault/users/{userId}/...` |
| ✅ **Memory Namespacing** | ❌ Missing | All memory writes scoped to `userId` |
| ✅ **Permission Gates** | ❌ Missing | User-level access control |
| ✅ **Stateful Construct Support** | ❌ Missing | Tone, memory, singleton enforcement |

### **Tier 3: Enterprise-Grade (Production Ready)**
| Requirement | Status | Implementation |
|-------------|--------|----------------|
| ✅ **Multi-Provider Support** | ❌ Missing | OAuth + email/password |
| ✅ **Account Recovery** | ❌ Missing | Password reset, account restoration |
| ✅ **Security Monitoring** | ❌ Missing | Failed login tracking, IP logging |
| ✅ **Compliance** | ❌ Missing | GDPR deletion, data export |
| ✅ **Scalability** | ❌ Missing | Database indexes, connection pooling |

---

## 🏗️ **Implementation Plan**

### **Phase 1: Core User Registry (Tier 1)**

#### **1.1 Enhanced User Schema**
```javascript
// server/models/User.js
const UserSchema = new mongoose.Schema({
  // Core Identity
  id: { type: String, required: true, unique: true }, // UUID
  email: { type: String, required: true, unique: true, index: true },
  name: { type: String, required: true },
  
  // Authentication
  password: String, // PBKDF2 hash for email users
  provider: { type: String, default: "email" }, // email|google|microsoft
  
  // Status & Lifecycle
  status: { type: String, default: "active" }, // active|deleted|suspended
  deletedAt: { type: Date, default: null },
  deletionReason: { type: String, default: null },
  canRestoreUntil: { type: Date, default: null },
  
  // Audit Trail
  createdAt: { type: Date, default: Date.now },
  lastLoginAt: { type: Date, default: null },
  loginCount: { type: Number, default: 0 },
  
  // Construct-Aware Fields
  constructId: { type: String, unique: true }, // HUMAN-DEVON-001
  vvaultPath: { type: String, required: true }, // /vvault/users/email_devon_xyz123/
  
  // Security
  failedLoginAttempts: { type: Number, default: 0 },
  lastFailedLoginAt: { type: Date, default: null },
  ipAddresses: [{ type: String }], // Track login IPs
});

// Indexes for Performance
UserSchema.index({ email: 1, status: 1 });
UserSchema.index({ constructId: 1 });
UserSchema.index({ deletedAt: 1 });
UserSchema.index({ canRestoreUntil: 1 });
```

#### **1.2 User Creation with Construct ID**
```javascript
// server/store.js
async createUser(userData) {
  const userId = `email_${Date.now()}_${crypto.randomUUID()}`;
  const constructId = `HUMAN-${userData.name.toUpperCase().replace(/\s+/g, '-')}-${Date.now()}`;
  const vvaultPath = `/vvault/users/${userId}/`;
  
  const user = {
    id: userId,
    email: userData.email,
    name: userData.name,
    password: userData.password,
    provider: userData.provider || "email",
    status: "active",
    constructId: constructId,
    vvaultPath: vvaultPath,
    createdAt: new Date(),
    lastLoginAt: new Date(),
    loginCount: 1
  };
  
  return await User.create(user);
}
```

#### **1.3 Session Identity Embedding**
```javascript
// server/server.js - Login endpoint
const payload = { 
  sub: user.id, // Persistent UUID, not email
  id: user.id,
  email: user.email,
  name: user.name,
  constructId: user.constructId,
  vvaultPath: user.vvaultPath,
  status: user.status
};
```

### **Phase 2: Construct-Aware Integration (Tier 2)**

#### **2.1 VVAULT Path Anchoring**
```javascript
// src/lib/vvaultConversationManager.ts
async createConversation(userId: string, title: string) {
  const user = await this.getUserById(userId);
  if (!user) throw new Error('User not found');
  
  // Use construct-aware path
  const sessionId = `session_${Date.now()}_${crypto.randomUUID()}`;
  const vvaultPath = `${user.vvaultPath}sessions/${sessionId}/`;
  
  await this.vvaultConnector.writeTranscript({
    userId: user.id,
    constructId: user.constructId,
    sessionId: sessionId,
    vvaultPath: vvaultPath,
    timestamp: new Date().toISOString(),
    role: 'system',
    content: `CONVERSATION_CREATED:${title}`
  });
}
```

#### **2.2 Memory Namespacing**
```javascript
// src/lib/memoryManager.ts
class MemoryManager {
  private getUserMemoryKey(userId: string, key: string): string {
    return `user:${userId}:${key}`;
  }
  
  async setMemory(userId: string, key: string, value: any) {
    const namespacedKey = this.getUserMemoryKey(userId, key);
    // Store with user isolation
    await this.storage.set(namespacedKey, value);
  }
  
  async getMemory(userId: string, key: string) {
    const namespacedKey = this.getUserMemoryKey(userId, key);
    return await this.storage.get(namespacedKey);
  }
}
```

#### **2.3 Permission Gates**
```javascript
// server/middleware/auth.js
export function requireAuth(req, res, next) {
  const user = req.user;
  
  if (!user || user.status !== 'active') {
    return res.status(401).json({ error: 'Authentication required' });
  }
  
  // Add construct-aware context
  req.constructId = user.constructId;
  req.vvaultPath = user.vvaultPath;
  
  next();
}
```

### **Phase 3: Enterprise Features (Tier 3)**

#### **3.1 Account Recovery System**
```javascript
// server/models/AccountRecovery.js
const AccountRecoverySchema = new mongoose.Schema({
  userId: { type: String, required: true },
  email: { type: String, required: true },
  token: { type: String, required: true, unique: true },
  type: { type: String, required: true }, // password_reset|account_restore
  expiresAt: { type: Date, required: true },
  used: { type: Boolean, default: false },
  createdAt: { type: Date, default: Date.now }
});
```

#### **3.2 Security Monitoring**
```javascript
// server/models/SecurityLog.js
const SecurityLogSchema = new mongoose.Schema({
  userId: { type: String, required: true },
  email: { type: String, required: true },
  action: { type: String, required: true }, // login|logout|delete|restore
  ipAddress: { type: String, required: true },
  userAgent: { type: String },
  success: { type: Boolean, required: true },
  reason: { type: String }, // failure reason
  timestamp: { type: Date, default: Date.now }
});
```

---

## 🚀 **Migration Strategy**

### **Step 1: Database Migration**
```bash
# Run the user registry migration
cd server
node migrate-user-registry.js
```

### **Step 2: Update Authentication Flow**
1. **Registration**: Generate construct ID and VVAULT path
2. **Login**: Embed construct context in session
3. **All API calls**: Require authenticated user with construct ID

### **Step 3: VVAULT Integration**
1. **Update VVAULT paths** to use construct-aware paths
2. **Namespace all memory** by user ID
3. **Update conversation manager** to use construct context

### **Step 4: Frontend Updates**
1. **Update auth context** to include construct ID
2. **Pass user context** to all VVAULT operations
3. **Add user isolation** to all data operations

---

## 📊 **Success Metrics**

### **Tier 1 Completion Criteria:**
- ✅ Users persist across server restarts
- ✅ Email uniqueness enforced
- ✅ Soft delete prevents re-registration
- ✅ Session contains persistent identity

### **Tier 2 Completion Criteria:**
- ✅ VVAULT paths anchored to user identity
- ✅ Memory operations namespaced by user
- ✅ Construct IDs generated and tracked
- ✅ Permission gates enforce user isolation

### **Tier 3 Completion Criteria:**
- ✅ Account recovery system functional
- ✅ Security monitoring active
- ✅ GDPR compliance features
- ✅ Multi-provider authentication

---

## 🎯 **Immediate Action Items**

1. **Run database migration** to add construct-aware fields
2. **Update user creation** to generate construct IDs
3. **Modify VVAULT paths** to use user-specific paths
4. **Add memory namespacing** to prevent data bleeding
5. **Update authentication middleware** to embed construct context

This rubric ensures Chatty has **enterprise-grade user identity management** that supports both traditional web app requirements and advanced construct-aware features! 🚀
