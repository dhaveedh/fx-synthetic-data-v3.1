const BASE = '/api';

export async function checkHealth() {
  const res = await fetch(`${BASE}/health`);
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
  return res.json();
}

export async function generateFromPrompt({ prompt, maxIterations = 3, driftTolerance = 0.05, maxRows = 5000 }) {
  const res = await fetch(`${BASE}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt,
      max_iterations: maxIterations,
      drift_tolerance: driftTolerance,
      max_rows: maxRows,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Generation failed: ${res.status}`);
  }
  return res.json();
}

export async function generateFromConfig({ config, maxIterations = 3, driftTolerance = 0.05, maxRows = 5000 }) {
  const res = await fetch(`${BASE}/generate/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      config,
      max_iterations: maxIterations,
      drift_tolerance: driftTolerance,
      max_rows: maxRows,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Generation failed: ${res.status}`);
  }
  return res.json();
}

export async function generateFromPromptStreaming({ prompt, maxIterations = 3, driftTolerance = 0.05, maxRows = 5000, onProgress }) {
  const res = await fetch(`${BASE}/generate/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      prompt,
      max_iterations: maxIterations,
      drift_tolerance: driftTolerance,
      max_rows: maxRows,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Generation failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === 'complete') return event.result;
      if (event.type === 'error') throw new Error(event.message);
      if (onProgress) onProgress(event);
    }
  }

  throw new Error('Stream ended without a result');
}

export function getDownloadUrl(filename) {
  return `${BASE}/download/${encodeURIComponent(filename)}`;
}
