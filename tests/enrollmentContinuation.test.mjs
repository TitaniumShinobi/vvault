import test from 'node:test';
import assert from 'node:assert/strict';
import { enrollmentCheckpoint } from '../src/utils/enrollmentContinuation.mjs';
test('refresh resumes every persisted checkpoint without mutation', () => {
 const base={pending:true,session_kind:'PENDING_ENROLLMENT',legal_receipts_current:false,passkey_registered:false,recovery_codes_ready:false};
 for(const [patch,step] of [[{},'consent'],[{legal_receipts_current:true},'passkey'],[{legal_receipts_current:true,passkey_registered:true},'recovery'],[{legal_receipts_current:true,passkey_registered:true,recovery_codes_ready:true},'activate'],[{legal_receipts_current:false,passkey_registered:true,recovery_codes_ready:true},'consent'],[{session_kind:'PENDING_DEVICE'},'device']]) {
  const status=Object.freeze({...base,...patch});assert.equal(enrollmentCheckpoint(status),step);
 }
});
test('missing, expired and incompatible state cannot grant progression',()=>{
 for(const status of [null,{pending:false},{pending:true},{pending:true,session_kind:'PENDING_ENROLLMENT',legal_receipts_current:true,passkey_registered:true}])assert.throws(()=>enrollmentCheckpoint(status));
});
