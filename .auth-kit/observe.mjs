import fs from 'node:fs';
import { CAPABILITIES } from './runtime/auth-capability-catalog.mjs';

const binding=JSON.parse(fs.readFileSync(0,'utf8'));
const profile=JSON.parse(fs.readFileSync('.auth-kit/product/VVAULT_PRODUCTION_AUTH.json','utf8'));
const matrix=JSON.parse(fs.readFileSync('.auth-kit/product/candidate-obligations.json','utf8'));
const mappings=JSON.parse(fs.readFileSync('.auth-kit/product/observer-mappings.json','utf8'));
const evidenceState=JSON.parse(fs.readFileSync('.auth-kit/product/evidence-state.json','utf8'));
const required=new Map();
for(const key of profile.requiredChecks){const split=key.lastIndexOf('/');const capability=key.slice(0,split),check=key.slice(split+1);if(!required.has(capability))required.set(capability,[]);required.get(capability).push(check);}
const capabilities={};
for(const contract of CAPABILITIES){
  const checks=required.get(contract.id);
  if(!checks){capabilities[contract.id]={applicability:'FALSE',reason:'Outside VVAULT_PRODUCTION_AUTH'};continue;}
  const observedChecks=Object.fromEntries(checks.filter(id=>evidenceState.genericChecks?.[`${contract.id}/${id}`]==='PASS').map(id=>[id,true]));
  const evidence=Array.isArray(evidenceState.genericEvidence?.[contract.id])?evidenceState.genericEvidence[contract.id]:[];
  capabilities[contract.id]={applicability:'TRUE',implementation:checks.every(id=>observedChecks[id]===true)&&evidence.length?'ESTABLISHED':'INDETERMINATE',evidence,checks:observedChecks};
}
const productCandidates=matrix.candidates.map(candidate=>{
  const evidence=evidenceState.candidates?.[candidate.id]||{};
  return {...candidate,observerMapping:mappings[candidate.id]||null,establishment:evidence.establishment||'INDETERMINATE',verification:evidence.verification||'INDETERMINATE',evidence:Array.isArray(evidence.evidence)?evidence.evidence:[]};
});
const independenceEvidence=evidenceState.independence||{};
const independence={
  wizardStopped:independenceEvidence.wizardStopped===true,
  authOrchestrationRequests:Number.isInteger(independenceEvidence.authOrchestrationRequests)?independenceEvidence.authOrchestrationRequests:null,
  runtimePassed:independenceEvidence.runtimePassed===true,
  evidence:Array.isArray(independenceEvidence.evidence)?independenceEvidence.evidence:[],
};
console.log(JSON.stringify({binding,adapter:{schemaVersion:1,authenticationRole:'IDENTITY_AUTHORITY',productId:binding.productId,revision:binding.revision,productProfile:{id:profile.id,requiredChecks:profile.requiredChecks},productCandidates,capabilities},independence}));
