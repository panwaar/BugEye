'use strict';

const AGENTS = ['rag', 'fetch', 'security', 'review', 'critic', 'fixes'];
const TERMINAL_EVENTS = new Set(['complete', 'error', 'rag_failed']);
const GREETING = 'Codebase indexed. Ask me anything about the code.';

// Repo whose index the chat talks to (the normalised name returned by the server).
let currentRepo = null;

const $ = (id) => document.getElementById(id);

// ── Rendering ────────────────────────────────────────────────

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// All model output is untrusted (it is shaped by the analysed repo), so it is always sanitised.
function renderMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text));
}

function renderDiff(code) {
  const lines = code.split('\n').map((line) => {
    let kind = 'neutral';
    if (line.startsWith('+') && !line.startsWith('+++')) kind = 'added';
    else if (line.startsWith('-') && !line.startsWith('---')) kind = 'removed';
    return `<span class="diff-line ${kind}">${escapeHtml(line)}</span>`;
  });
  return `<div class="diff-view">${lines.join('')}</div>`;
}

// Markdown, plus coloured ```diff blocks and red/green "Current code" / "Suggested fix" blocks.
function renderReport(text) {
  const withDiffs = text.replace(/```diff\n([\s\S]*?)```/g, (_, code) => renderDiff(code));
  const container = document.createElement('div');
  container.innerHTML = renderMarkdown(withDiffs);

  let pending = null;
  container.childNodes.forEach((node) => {
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const label = node.textContent.trim().toLowerCase();
    if (label.includes('current code')) pending = 'removed';
    else if (label.includes('suggested fix') || label.includes('suggested code')) pending = 'added';
    if (node.tagName === 'PRE' && pending) {
      node.classList.add(`code-${pending}`);
      pending = null;
    }
  });
  return container.innerHTML;
}

// ── Pipeline cards ───────────────────────────────────────────

function resetPipeline() {
  AGENTS.forEach((agent) => {
    $(`card-${agent}`).className = 'agent-card';
    $(`status-${agent}`).textContent = 'Waiting...';
    $(`steps-${agent}`).replaceChildren();
  });
}

function finishSteps(agent, succeeded) {
  $(`steps-${agent}`).querySelectorAll('.step-line').forEach((line) => {
    line.classList.remove('current');
    if (succeeded) line.classList.add('done-step');
    const spinner = line.querySelector('.spin');
    if (spinner) spinner.className = 'dot';
  });
}

function setAgentState(agent, state, message) {
  $(`card-${agent}`).className = `agent-card ${state}`;
  const prefix = { done: 'Done — ', failed: 'Failed — ' }[state] || '';
  $(`status-${agent}`).textContent = prefix + message;
  if (state !== 'running') finishSteps(agent, state === 'done');
}

function addStep(agent, message) {
  finishSteps(agent, true);
  const line = document.createElement('div');
  line.className = 'step-line current';
  const spinner = document.createElement('span');
  spinner.className = 'spin';
  line.append(spinner, document.createTextNode(message));
  $(`steps-${agent}`).appendChild(line);
  setTimeout(() => line.classList.add('visible'), 30);
}

// ── Banners and button ───────────────────────────────────────

function showError(message) {
  const banner = $('error-banner');
  banner.textContent = message;
  banner.classList.add('active');
}

function hideBanners() {
  $('error-banner').classList.remove('active');
  $('rag-banner').classList.remove('active');
}

function setBusy(busy) {
  const btn = $('btn');
  btn.disabled = busy;
  btn.textContent = busy ? 'Running agents...' : 'Analyze Repository';
}

// ── API ──────────────────────────────────────────────────────

const JSON_HEADERS = { 'Content-Type': 'application/json' };

async function errorFrom(response) {
  try {
    const data = await response.json();
    if (data && data.error) return data.error;
  } catch (_) { /* not JSON */ }
  return `Request failed (HTTP ${response.status}).`;
}

// POSTs to the review endpoint and calls onEvent for each server-sent event.
async function streamReview(repo, pr, onEvent) {
  const response = await fetch('/api/review', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ repo, pr }),
  });
  if (!response.ok) throw new Error(await errorFrom(response));

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      block.split('\n')
        .filter((line) => line.startsWith('data: '))
        .forEach((line) => onEvent(JSON.parse(line.slice(6))));
    }
  }
}

// ── Review flow ──────────────────────────────────────────────

function handleEvent(event) {
  switch (event.event) {
    case 'agent_start': setAgentState(event.agent, 'running', event.message); break;
    case 'agent_step': addStep(event.agent, event.message); break;
    case 'agent_done': setAgentState(event.agent, 'done', event.message); break;
    case 'agent_failed': setAgentState(event.agent, 'failed', event.message); break;
    case 'rag_failed':
      $('rag-banner-msg').textContent = event.message;
      $('rag-banner').classList.add('active');
      break;
    case 'error': showError(event.message); break;
    case 'complete': showResults(event); break;
  }
}

function showResults(result) {
  currentRepo = result.repo;
  $('body-review').innerHTML = renderReport(result.review);
  $('body-security').innerHTML = renderReport(result.security);
  $('body-fixes').innerHTML = renderReport(result.fixes);
  resetChat();
  $('main-layout').classList.add('active');
  $('main-layout').scrollIntoView({ behavior: 'smooth' });
}

async function startReview(event) {
  event.preventDefault();
  const repo = $('repo').value.trim();
  const pr = $('pr').value.trim();
  hideBanners();
  $('main-layout').classList.remove('active');
  if (!repo) {
    showError('Please enter a repository name or GitHub URL.');
    return;
  }

  resetPipeline();
  setBusy(true);
  $('pipeline').classList.add('active');
  let finished = false;
  try {
    await streamReview(repo, pr, (evt) => {
      if (TERMINAL_EVENTS.has(evt.event)) finished = true;
      handleEvent(evt);
    });
    if (!finished) showError('The connection closed before the analysis finished. Please try again.');
  } catch (err) {
    showError(err.message || 'Could not reach the server. Please try again.');
  } finally {
    setBusy(false);
  }
}

// ── Chat ─────────────────────────────────────────────────────

function addMessage(text, isUser) {
  const messages = $('chat-messages');
  const msg = document.createElement('div');
  msg.className = `msg ${isUser ? 'user' : 'bot'}`;
  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar';
  avatar.textContent = isUser ? 'U' : 'A';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  if (isUser) bubble.textContent = text;
  else bubble.innerHTML = renderMarkdown(text);
  msg.append(avatar, bubble);
  messages.appendChild(msg);
  messages.scrollTop = messages.scrollHeight;
  return msg;
}

function resetChat() {
  $('chat-messages').replaceChildren();
  addMessage(GREETING, false);
}

function addTyping() {
  const msg = addMessage('', false);
  msg.querySelector('.msg-bubble').innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
  return msg;
}

async function sendChat(event) {
  if (event) event.preventDefault();
  const input = $('chat-input');
  const sendBtn = $('chat-send');
  const question = input.value.trim();
  if (!question || !currentRepo) return;

  input.value = '';
  sendBtn.disabled = true;
  addMessage(question, true);
  const typing = addTyping();
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ repo: currentRepo, question }),
    });
    typing.remove();
    if (response.ok) addMessage((await response.json()).answer, false);
    else addMessage(`Error: ${await errorFrom(response)}`, false);
  } catch (_) {
    typing.remove();
    addMessage('Something went wrong. Please try again.', false);
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

// ── Wiring ───────────────────────────────────────────────────

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach((c) => c.classList.toggle('active', c.id === `tab-${name}`));
}

document.addEventListener('DOMContentLoaded', () => {
  $('review-form').addEventListener('submit', startReview);
  $('chat-form').addEventListener('submit', sendChat);
  document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));
  document.querySelectorAll('.suggestion').forEach((btn) => btn.addEventListener('click', () => {
    $('chat-input').value = btn.textContent;
    sendChat();
  }));
});
