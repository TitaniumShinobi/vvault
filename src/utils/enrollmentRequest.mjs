export async function requestEnrollmentJson(path, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  let timer;
  try {
    return await Promise.race([
      (async () => {
        const response = await fetch(path, {
          ...options,
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
          signal: controller.signal,
        });
        const payload = await response.json();
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
          throw new Error('Enrollment returned an invalid response. Please retry.');
        }
        if (!response.ok) { const error = new Error(payload.error || 'Enrollment request failed'); error.status = response.status; throw error; }
        return payload;
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error('Enrollment request timed out. Please retry.'));
          controller.abort();
        }, timeoutMs);
      }),
    ]);
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error('Enrollment returned an invalid response. Please retry.');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
