// Product-local release admission. Established products require full VERIFY;
// the unestablished VVAULT candidate may only publish after its bounded,
// deterministic pre-baseline suite passes. This never establishes a baseline.
import './product/prebaseline-release-gate.mjs';
