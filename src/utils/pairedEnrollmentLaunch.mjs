// A navigation hint is never admission. Only the server's successful activation
// response supplies this plan; each destination still enforces its own gates.
export function validatedPairedLaunch(value) {
  if (!value || value.version !== 'paired-signup-launch/v1' || !['chatty', 'vvault'].includes(value.initiator)) return null;
  try {
    for (const field of ['currentUrl', 'companionUrl']) {
      const url = new URL(value[field]);
      if (url.username || url.password || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname)))) return null;
    }
    return value;
  } catch { return null; }
}

export function reserveCompanionTab(browserWindow) {
  try {
    const tab = browserWindow.open('about:blank', '_blank');
    if (tab) tab.opener = null;
    return tab;
  } catch { return null; }
}

export function launchVerifiedPair(result, tab, browserWindow) {
  const plan = result?.success === true && result?.completed === true ? validatedPairedLaunch(result.pairedLaunch) : null;
  if (!plan) {
    try { tab?.close(); } catch { /* Already closed. */ }
    return { launched: false, plan: null };
  }
  if (!tab || tab.closed) return { launched: false, plan };
  try {
    tab.location.replace(plan.companionUrl);
  } catch {
    try { tab.close(); } catch { /* Already closed. */ }
    return { launched: false, plan };
  }
  browserWindow.location.assign(plan.currentUrl);
  return { launched: true, plan };
}
