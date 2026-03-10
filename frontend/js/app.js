const API = window.STUDYBOT_API_BASE || 'http://localhost:5000/api';
let token = localStorage.getItem('sb_token');
let currentUser = JSON.parse(localStorage.getItem('sb_user') || 'null');
let currentSubjectId = null;
let currentMaterialId = null;
let currentSessionId = null;
let currentTestId = null;
let testStartTime = null;
let testTimerInterval = null;
let testQuestions = [];
let userAnswers = {};
let subjectsCache = [];
let aiSettings = null;
let speechRecognition = null;
let isListening = false;
let isMuted = false;

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

async function apiForm(path, formData) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Upload failed');
  return data;
}

document.addEventListener('DOMContentLoaded', () => {
  if (token && currentUser) {
    showApp();
  } else {
    document.getElementById('authScreen').classList.remove('hidden');
  }
});

function showRegister() {
  document.getElementById('loginForm').classList.remove('active');
  document.getElementById('registerForm').classList.add('active');
}

function showLogin() {
  document.getElementById('registerForm').classList.remove('active');
  document.getElementById('loginForm').classList.add('active');
}

async function login() {
  const email = document.getElementById('loginEmail').value.trim();
  const password = document.getElementById('loginPassword').value;
  const errEl = document.getElementById('loginError');
  errEl.classList.add('hidden');

  try {
    const data = await api('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
    setAuth(data);
    showApp();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

async function register() {
  const name = document.getElementById('regName').value.trim();
  const email = document.getElementById('regEmail').value.trim();
  const password = document.getElementById('regPassword').value;
  const errEl = document.getElementById('registerError');
  errEl.classList.add('hidden');

  try {
    const data = await api('/auth/register', { method: 'POST', body: JSON.stringify({ name, email, password }) });
    setAuth(data);
    showApp();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

function googleLogin() {
  api('/auth/google-config')
    .then(cfg => {
      const clientId = cfg?.client_id || '';
      if (!clientId) {
        alert('Google login is not configured on server. Set GOOGLE_CLIENT_ID in backend/.env.');
        return;
      }

      google.accounts.id.initialize({
        client_id: clientId,
        callback: async response => {
          try {
            const data = await api('/auth/google', {
              method: 'POST',
              body: JSON.stringify({ credential: response.credential })
            });
            setAuth(data);
            showApp();
          } catch (e) {
            alert('Google login failed: ' + e.message);
          }
        }
      });
      google.accounts.id.prompt();
    })
    .catch(err => alert('Could not load Google login config: ' + err.message));
}

function setAuth(data) {
  token = data.token;
  currentUser = data.user;
  localStorage.setItem('sb_token', token);
  localStorage.setItem('sb_user', JSON.stringify(currentUser));
}

function logout() {
  token = null;
  currentUser = null;
  localStorage.removeItem('sb_token');
  localStorage.removeItem('sb_user');
  location.reload();
}

function showApp() {
  document.getElementById('authScreen').classList.add('hidden');
  document.getElementById('appScreen').classList.remove('hidden');

  document.getElementById('userNameSb').textContent = currentUser?.name || 'Student';
  document.getElementById('userEmailSb').textContent = currentUser?.email || '';
  document.getElementById('userAvatarSb').textContent = (currentUser?.name?.[0] || 'S').toUpperCase();

  initVoiceSupport();
  loadAiSettings();
  loadSubjects();
  loadChatHistory();
  loadProgress();
}

function switchTab(tab, btn) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(`tab-${tab}`).classList.add('active');
  btn.classList.add('active');

  if (tab === 'tests') loadTestHistory();
  if (tab === 'progress') loadProgress();
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}

async function loadAiSettings() {
  const providerSelect = document.getElementById('aiProviderSelect');
  const modelSelect = document.getElementById('aiModelSelect');
  if (!providerSelect || !modelSelect) return;

  try {
    aiSettings = await api('/chat/ai-settings');
    providerSelect.value = aiSettings.provider || 'auto';
    rebuildModelOptions(providerSelect.value, aiSettings);

    if (providerSelect.value === 'gemini') {
      modelSelect.value = aiSettings.gemini_model || '';
    } else if (providerSelect.value === 'openai') {
      modelSelect.value = aiSettings.openai_model || '';
    }
  } catch (e) {
    console.error('AI settings load failed:', e);
  }
}

function rebuildModelOptions(provider, settings) {
  const modelSelect = document.getElementById('aiModelSelect');
  if (!modelSelect || !settings) return;

  let models = [];
  if (provider === 'gemini') models = settings.models?.gemini || [];
  if (provider === 'openai') models = settings.models?.openai || [];

  if (!models.length) {
    modelSelect.innerHTML = '<option value="">Default</option>';
    modelSelect.disabled = provider === 'auto';
    return;
  }

  modelSelect.innerHTML = models.map(m => `<option value="${escHtml(m)}">${escHtml(m)}</option>`).join('');
  modelSelect.disabled = provider === 'auto';
}

function getSelectedAiOptions() {
  const providerSelect = document.getElementById('aiProviderSelect');
  const modelSelect = document.getElementById('aiModelSelect');
  return {
    ai_provider: providerSelect?.value || 'auto',
    ai_model: modelSelect?.value || ''
  };
}

async function onAiProviderChange() {
  if (!aiSettings) return;
  const provider = document.getElementById('aiProviderSelect').value;
  rebuildModelOptions(provider, aiSettings);
  await saveAiSettings();
}

async function saveAiSettings() {
  const provider = document.getElementById('aiProviderSelect')?.value || 'auto';
  const model = document.getElementById('aiModelSelect')?.value || '';

  try {
    aiSettings = await api('/chat/ai-settings', {
      method: 'PUT',
      body: JSON.stringify({ provider, model })
    });

    rebuildModelOptions(provider, aiSettings);
    if (provider === 'gemini') {
      document.getElementById('aiModelSelect').value = aiSettings.gemini_model || '';
    } else if (provider === 'openai') {
      document.getElementById('aiModelSelect').value = aiSettings.openai_model || '';
    }
  } catch (e) {
    alert('Could not save AI settings: ' + e.message);
  }
}

function initVoiceSupport() {
  const btnMic = document.getElementById('btnMic');
  const btnMute = document.getElementById('btnMute');
  if (btnMute) btnMute.classList.toggle('active', isMuted);

  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) {
    if (btnMic) {
      btnMic.disabled = true;
      btnMic.title = 'Speech recognition not supported in this browser';
    }
    return;
  }

  speechRecognition = new Rec();
  speechRecognition.lang = 'en-US';
  speechRecognition.continuous = false;
  speechRecognition.interimResults = true;

  speechRecognition.onstart = () => {
    isListening = true;
    updateMicButton();
  };

  speechRecognition.onend = () => {
    isListening = false;
    updateMicButton();
  };

  speechRecognition.onerror = () => {
    isListening = false;
    updateMicButton();
  };

  speechRecognition.onresult = evt => {
    let transcript = '';
    for (let i = evt.resultIndex; i < evt.results.length; i += 1) {
      transcript += evt.results[i][0].transcript;
    }
    const input = document.getElementById('chatInput');
    if (input) input.value = transcript.trim();
  };
}

function updateMicButton() {
  const btnMic = document.getElementById('btnMic');
  if (!btnMic) return;
  btnMic.classList.toggle('active', isListening);
  btnMic.textContent = isListening ? 'Stop' : 'Mic';
}

function toggleMic() {
  if (!speechRecognition) return;

  if (isListening) {
    speechRecognition.stop();
    return;
  }

  try {
    speechRecognition.start();
  } catch (e) {
    console.error(e);
  }
}

function toggleMute() {
  isMuted = !isMuted;
  const btnMute = document.getElementById('btnMute');
  if (btnMute) {
    btnMute.classList.toggle('active', isMuted);
    btnMute.textContent = isMuted ? 'Unmute' : 'Mute';
  }

  if (isMuted && window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
}

function speakText(text) {
  if (isMuted || !window.speechSynthesis || !text) return;

  const clean = String(text)
    .replace(/https?:\/\/\S+/g, '')
    .replace(/\*\*/g, '')
    .replace(/`/g, '')
    .trim();

  if (!clean) return;

  const utterance = new SpeechSynthesisUtterance(clean.slice(0, 450));
  utterance.rate = 1;
  utterance.pitch = 1;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

async function loadSubjects() {
  try {
    const subjects = await api('/materials/subjects');
    subjectsCache = subjects;
    renderSubjectSelects(subjects);

    if (!currentSubjectId && subjects.length) {
      currentSubjectId = subjects[0].id;
      const sel = document.getElementById('materialSelect');
      if (sel) sel.value = String(currentSubjectId);
    }

    if (currentSubjectId) {
      await loadMaterialsForSubject(currentSubjectId);
      const sub = subjects.find(s => s.id === Number(currentSubjectId));
      document.getElementById('chatSubtitle').textContent = sub
        ? `Chatting about subject: ${sub.name}`
        : 'Select a subject to start studying';
    } else {
      renderMaterialsList([]);
      document.getElementById('chatSubtitle').textContent = 'Create a subject to start studying';
    }
  } catch (e) {
    console.error(e);
  }
}

function renderSubjectSelects(subjects) {
  const options = '<option value="">Select subject</option>' +
    subjects.map(s => `<option value="${s.id}">${escHtml(s.name)} (${s.material_count || 0})</option>`).join('');

  const materialSelect = document.getElementById('materialSelect');
  if (materialSelect) materialSelect.innerHTML = options;

  const testSelect = document.getElementById('testMaterialSelect');
  if (testSelect) testSelect.innerHTML = options;

  const uploadSelect = document.getElementById('uploadSubjectSelect');
  if (uploadSelect) uploadSelect.innerHTML = options;

  if (currentSubjectId) {
    if (materialSelect) materialSelect.value = String(currentSubjectId);
    if (testSelect) testSelect.value = String(currentSubjectId);
    if (uploadSelect) uploadSelect.value = String(currentSubjectId);
  }
}

async function createSubjectPrompt() {
  const name = prompt('Enter new subject name:');
  if (!name || !name.trim()) return;

  try {
    const created = await api('/materials/subjects', { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
    currentSubjectId = created.id;
    await loadSubjects();
    alert('Subject created.');
  } catch (e) {
    alert(e.message);
  }
}

async function renameSubjectPrompt() {
  if (!currentSubjectId) {
    alert('Select a subject first.');
    return;
  }
  const subject = subjectsCache.find(s => s.id === Number(currentSubjectId));
  const name = prompt('Rename subject:', subject?.name || '');
  if (!name || !name.trim()) return;

  try {
    await api(`/materials/subjects/${currentSubjectId}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) });
    await loadSubjects();
    alert('Subject renamed.');
  } catch (e) {
    alert(e.message);
  }
}

async function deleteSubjectPrompt() {
  if (!currentSubjectId) {
    alert('Select a subject first.');
    return;
  }
  const subject = subjectsCache.find(s => s.id === Number(currentSubjectId));
  if (!confirm(`Delete subject "${subject?.name || ''}" and all its files?`)) return;
  try {
    await api(`/materials/subjects/${currentSubjectId}`, { method: 'DELETE' });
    currentSubjectId = null;
    currentMaterialId = null;
    await loadSubjects();
    clearChat(true);
  } catch (e) {
    alert(e.message);
  }
}

async function selectSubject(id) {
  currentSubjectId = id ? Number(id) : null;
  currentMaterialId = null;
  currentSessionId = null;
  clearChat(true);

  if (!currentSubjectId) {
    renderMaterialsList([]);
    document.getElementById('chatSubtitle').textContent = 'Select a subject to start studying';
    return;
  }

  const subject = subjectsCache.find(s => s.id === Number(currentSubjectId));
  document.getElementById('chatSubtitle').textContent = subject
    ? `Chatting about subject: ${subject.name}`
    : 'Chatting about selected subject';

  await loadMaterialsForSubject(currentSubjectId);
}

async function loadMaterialsForSubject(subjectId) {
  try {
    const materials = await api(`/materials/subjects/${subjectId}/materials`);
    renderMaterialsList(materials);
  } catch (e) {
    console.error(e);
  }
}

function renderMaterialsList(materials) {
  const el = document.getElementById('materialsList');
  if (!materials.length) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">M</div><p>No materials in this subject yet.</p></div>';
    return;
  }

  el.innerHTML = materials.map(m => {
    const topics = tryParseTopics(m.key_topics);
    return `
      <div class="material-card">
        <div class="mc-title" title="${escHtml(m.title)}">${escHtml(m.title)}</div>
        <div class="mc-subject">${escHtml(m.subject || 'No subject')} | ${formatSize(m.file_size)}</div>
        <div class="mc-topics">
          ${topics.slice(0, 3).map(t => `<span class="mc-topic-chip">${escHtml(t)}</span>`).join('')}
          ${topics.length > 3 ? `<span class="mc-topic-chip">+${topics.length - 3}</span>` : ''}
        </div>
        <div class="mc-actions">
          <button class="mc-btn" onclick="chatWithMaterial(${m.id})">Chat</button>
          <button class="mc-btn" onclick="generateTestFromCard(${m.id})">Test</button>
          <button class="mc-btn" onclick="renameMaterialPrompt(${m.id}, ${JSON.stringify(m.title || '')})">Rename</button>
          <button class="mc-btn" onclick="moveMaterialPrompt(${m.id})">Move</button>
          <button class="mc-btn danger" onclick="deleteMaterial(${m.id})">Delete</button>
        </div>
      </div>`;
  }).join('');
}

function chatWithMaterial(mid) {
  currentMaterialId = mid;
  currentSessionId = null;
  switchTab('chat', document.querySelector('[data-tab="chat"]'));
  clearChat(true);
}

function generateTestFromCard(mid) {
  currentMaterialId = mid;
  if (currentSubjectId) {
    const testSelect = document.getElementById('testMaterialSelect');
    if (testSelect) testSelect.value = String(currentSubjectId);
  }
  switchTab('tests', document.querySelector('[data-tab="tests"]'));
  showTestModal();
}

async function deleteMaterial(id) {
  if (!confirm('Delete this material? This cannot be undone.')) return;
  try {
    await api(`/materials/${id}`, { method: 'DELETE' });
    if (currentSubjectId) await loadMaterialsForSubject(currentSubjectId);
    await loadSubjects();
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

function showUploadModal() {
  const uploadSelect = document.getElementById('uploadSubjectSelect');
  if (uploadSelect && currentSubjectId) uploadSelect.value = String(currentSubjectId);
  document.getElementById('uploadModal').classList.remove('hidden');
}

function fileSelected(input) {
  const files = Array.from(input.files || []);
  const el = document.getElementById('fileSelectedName');
  if (!files.length) {
    el.classList.add('hidden');
    return;
  }

  if (files.length === 1) {
    el.textContent = `${files[0].name} (${formatSize(files[0].size)})`;
  } else {
    const total = files.reduce((s, f) => s + (f.size || 0), 0);
    el.textContent = `${files.length} files selected (${formatSize(total)})`;
  }
  el.classList.remove('hidden');
}

function dragOver(e) {
  e.preventDefault();
  document.getElementById('fileDrop').classList.add('dragover');
}

function dropFile(e) {
  e.preventDefault();
  document.getElementById('fileDrop').classList.remove('dragover');
  const files = e.dataTransfer.files;
  if (files && files.length) {
    document.getElementById('fileInput').files = files;
    fileSelected(document.getElementById('fileInput'));
  }
}

async function uploadMaterial() {
  const subjectId = document.getElementById('uploadSubjectSelect').value;
  const manualSubjectName = document.getElementById('uploadSubject').value.trim();
  const customTitle = document.getElementById('uploadTitle').value.trim();
  const files = Array.from(document.getElementById('fileInput').files || []);
  const errEl = document.getElementById('uploadError');
  errEl.classList.add('hidden');

  if (!files.length) {
    errEl.textContent = 'Please select at least one file.';
    errEl.classList.remove('hidden');
    return;
  }

  if (!subjectId && !manualSubjectName) {
    errEl.textContent = 'Select subject or type new subject name.';
    errEl.classList.remove('hidden');
    return;
  }

  showLoading(`Uploading ${files.length} file(s)...`);
  try {
    for (const file of files) {
      const form = new FormData();
      form.append('file', file);
      form.append('title', customTitle || file.name);
      if (subjectId) form.append('subject_id', subjectId);
      if (manualSubjectName) form.append('subject', manualSubjectName);
      await apiForm('/materials/upload', form);
    }

    closeModal('uploadModal');
    document.getElementById('uploadTitle').value = '';
    document.getElementById('uploadSubject').value = '';
    document.getElementById('fileInput').value = '';
    document.getElementById('fileSelectedName').classList.add('hidden');

    await loadSubjects();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  } finally {
    hideLoading();
  }
}

async function loadChatHistory() {
  try {
    const sessions = await api('/chat/sessions');
    const el = document.getElementById('chatHistoryList');
    el.innerHTML = sessions.slice(0, 10).map(s => `
      <button class="history-item ${s.id === currentSessionId ? 'active' : ''}" onclick="loadSession(${s.id})">
        ${escHtml(s.session_name || 'Chat session')}
      </button>`).join('');
  } catch (e) {
    console.error(e);
  }
}

async function loadSession(sid) {
  currentSessionId = sid;
  showLoading('Loading chat...');
  try {
    const msgs = await api(`/chat/sessions/${sid}`);
    clearChat(false);
    msgs.forEach(m => appendMessage(m.role, m.content, m.source));
    await loadChatHistory();
  } catch (e) {
    alert(e.message);
  } finally {
    hideLoading();
  }
}

function clearChat(keepWelcome = true) {
  const el = document.getElementById('chatMessages');
  if (!keepWelcome) {
    el.innerHTML = '';
    return;
  }

  el.innerHTML = '<div class="welcome-card"><h3>Hello! I am StudyBot</h3><p>Select a subject, upload files, and ask questions from those materials.</p></div>';
}

function newChat() {
  currentSessionId = null;
  clearChat(true);
  document.querySelectorAll('.history-item').forEach(b => b.classList.remove('active'));
}

async function sendMessage() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';

  if (!currentSubjectId && !currentMaterialId) {
    appendMessage('assistant', 'Please select a subject first.', 'ai');
    return;
  }

  appendMessage('user', text, 'user');
  const typing = appendTyping();

  try {
    const ai = getSelectedAiOptions();
    const data = await api('/chat/message', {
      method: 'POST',
      body: JSON.stringify({
        message: text,
        material_id: currentMaterialId,
        subject_id: currentSubjectId,
        session_id: currentSessionId,
        ...ai,
      })
    });

    currentSessionId = data.session_id;
    typing.remove();
    appendMessage('assistant', data.answer, data.source);
    await loadChatHistory();
  } catch (e) {
    typing.remove();
    appendMessage('assistant', 'Error: ' + e.message, 'ai');
  }
}

function appendMessage(role, content, source = 'ai') {
  const el = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  const sourceTag = source && source !== 'ai'
    ? `<span class="msg-source source-${source}">${source === 'material' ? 'From material' : source}</span>`
    : '';

  div.innerHTML = `<div><div class="msg-bubble">${formatMarkdown(content || '')}</div>${sourceTag}</div>`;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;

  if (role === 'assistant') {
    speakText(content || '');
  }

  return div;
}

function appendTyping() {
  const el = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg assistant typing-indicator';
  div.innerHTML = '<div><div class="msg-bubble">...</div></div>';
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
  return div;
}

async function loadKeyTopics() {
  if (!currentSubjectId) {
    alert('Select a subject first.');
    return;
  }

  showLoading('Extracting key topics...');
  try {
    const data = await api(`/materials/subjects/${currentSubjectId}/topics`);
    const topics = Array.isArray(data.key_topics) ? data.key_topics : [];
    const el = document.getElementById('topicsContent');
    el.innerHTML = `<h4>Key Topics</h4><div style="display:flex;flex-wrap:wrap;gap:8px;">${topics.map(t => `<span class="mc-topic-chip">${escHtml(t)}</span>`).join('')}</div>`;
    document.getElementById('topicsModal').classList.remove('hidden');
  } catch (e) {
    alert(e.message);
  } finally {
    hideLoading();
  }
}

async function loadImportantQuestions() {
  if (!currentSubjectId) {
    alert('Select a subject first.');
    return;
  }

  showLoading('Generating important questions...');
  try {
    const ai = getSelectedAiOptions();
    const q = `?ai_provider=${encodeURIComponent(ai.ai_provider)}&ai_model=${encodeURIComponent(ai.ai_model || '')}`;
    const data = await api(`/chat/important-questions-by-subject/${currentSubjectId}${q}`);
    const qs = data.questions || [];
    const el = document.getElementById('topicsContent');
    el.innerHTML = `<h4>Important Questions</h4>${qs.map((item, i) => `<div class="question-card"><div class="q-number">Q${i + 1}</div><div class="q-text">${escHtml(item.question || '')}</div></div>`).join('')}`;
    document.getElementById('topicsModal').classList.remove('hidden');
  } catch (e) {
    alert(e.message);
  } finally {
    hideLoading();
  }
}

function showTestModal() {
  const dst = document.getElementById('testMaterialSelect');
  const src = document.getElementById('materialSelect');
  if (dst && src) dst.innerHTML = src.innerHTML;
  if (currentSubjectId && dst) dst.value = String(currentSubjectId);
  document.getElementById('testModal').classList.remove('hidden');
}

async function generateTest() {
  const subjectId = document.getElementById('testMaterialSelect').value;
  if (!subjectId) {
    alert('Please select a subject.');
    return;
  }

  const type = document.querySelector('input[name="testType"]:checked').value;
  const count = parseInt(document.getElementById('qCount').value, 10);
  const diff = document.querySelector('input[name="difficulty"]:checked').value;

  closeModal('testModal');
  showLoading('Generating your test...');
  try {
    const ai = getSelectedAiOptions();
    const data = await api('/tests/generate', {
      method: 'POST',
      body: JSON.stringify({
        subject_id: Number(subjectId),
        material_id: currentMaterialId,
        type,
        count,
        difficulty: diff,
        ...ai,
      })
    });
    currentTestId = data.test_id;
    testQuestions = data.questions || [];
    userAnswers = {};
    startTest(data.title);
  } catch (e) {
    alert('Error generating test: ' + e.message);
  } finally {
    hideLoading();
  }
}

function startTest(title) {
  document.getElementById('testViewTitle').textContent = title;
  const body = document.getElementById('testViewBody');
  body.innerHTML = testQuestions.map((q, i) => renderQuestion(q, i)).join('');
  document.getElementById('testView').classList.remove('hidden');

  testStartTime = Date.now();
  clearInterval(testTimerInterval);
  testTimerInterval = setInterval(() => {
    const s = Math.floor((Date.now() - testStartTime) / 1000);
    const m = String(Math.floor(s / 60)).padStart(2, '0');
    const sec = String(s % 60).padStart(2, '0');
    document.getElementById('testTimer').textContent = `${m}:${sec}`;
  }, 1000);
}

function renderQuestion(q, i) {
  if (q.options) {
    return `<div class="question-card"><div class="q-number">Question ${i + 1}</div><div class="q-text">${escHtml(q.question)}</div><div class="options">${Object.entries(q.options).map(([k, v]) => `<button class="option-btn" id="opt-${i}-${k}" onclick="selectOption(${i}, '${k}')"><span class="option-key">${k}</span>${escHtml(v)}</button>`).join('')}</div></div>`;
  }

  return `<div class="question-card"><div class="q-number">Question ${i + 1}</div><div class="q-text">${escHtml(q.question)}</div><textarea class="short-answer-input" oninput="userAnswers['${i}']=this.value"></textarea></div>`;
}

function selectOption(qIdx, key) {
  userAnswers[qIdx] = key;
  const q = testQuestions[qIdx];
  Object.keys(q.options).forEach(k => {
    const btn = document.getElementById(`opt-${qIdx}-${k}`);
    if (btn) btn.classList.toggle('selected', k === key);
  });
}

async function submitTest() {
  if (!confirm('Submit your test?')) return;
  clearInterval(testTimerInterval);
  const timeTaken = Math.floor((Date.now() - testStartTime) / 1000);
  showLoading('Evaluating...');
  try {
    const result = await api(`/tests/${currentTestId}/submit`, {
      method: 'POST',
      body: JSON.stringify({ answers: userAnswers, time_taken: timeTaken })
    });
    closeModal('testView');
    showResults(result);
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    hideLoading();
  }
}

function showResults(r) {
  const el = document.getElementById('resultsContent');
  el.innerHTML = `<div class="result-summary"><div class="result-score">${r.score}%</div><div class="result-grade">Grade: ${r.grade}</div><div class="result-stats">${r.correct} / ${r.total} correct</div></div>`;
  document.getElementById('resultsModal').classList.remove('hidden');
  loadTestHistory();
}

async function loadTestHistory() {
  try {
    const rows = await api('/tests/history');
    const el = document.getElementById('testHistory');
    if (!rows.length) {
      el.innerHTML = '<div class="empty-state"><p>No tests taken yet.</p></div>';
      return;
    }

    el.innerHTML = rows.map(r => `<div class="test-record"><div><div class="tr-title">${escHtml(r.title)}</div><div class="tr-meta">${escHtml((r.test_type || '').replace('_', ' '))} | ${r.correct_answers}/${r.total_questions}</div></div><div class="score-badge">${parseFloat(r.score).toFixed(1)}%</div></div>`).join('');
  } catch (e) {
    console.error(e);
  }
}

async function loadProgress() {
  try {
    const data = await api('/progress/');
    const p = data.progress || {};
    document.getElementById('progressContent').innerHTML = `
      <div class="stat-card"><div class="stat-value">${p.total_tests || 0}</div><div class="stat-label">Tests Taken</div></div>
      <div class="stat-card"><div class="stat-value">${p.avg_score ? parseFloat(p.avg_score).toFixed(1) + '%' : '-'}</div><div class="stat-label">Average Score</div></div>
      <div class="stat-card"><div class="stat-value">${p.materials_uploaded || 0}</div><div class="stat-label">Materials Uploaded</div></div>
      <div class="stat-card"><div class="stat-value">${p.chat_sessions || 0}</div><div class="stat-label">Chat Sessions</div></div>`;
  } catch (e) {
    console.error(e);
  }
}

function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
  if (id === 'testView') clearInterval(testTimerInterval);
}

function showLoading(text = 'Processing...') {
  document.getElementById('loadingText').textContent = text;
  document.getElementById('loadingOverlay').classList.remove('hidden');
}

function hideLoading() {
  document.getElementById('loadingOverlay').classList.add('hidden');
}

document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.add('hidden');
    clearInterval(testTimerInterval);
  }
});

function tryParseTopics(raw) {
  if (!raw) return [];
  try {
    if (Array.isArray(raw)) return raw;
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

function formatSize(bytes) {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatMarkdown(text) {
  return String(text || '')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>');
}

async function renameMaterialPrompt(mid, currentTitle) {
  const next = prompt('New file title:', currentTitle || '');
  if (!next || !next.trim()) return;
  try {
    await api(`/materials/${mid}`, { method: 'PUT', body: JSON.stringify({ title: next.trim() }) });
    if (currentSubjectId) await loadMaterialsForSubject(currentSubjectId);
  } catch (e) {
    alert(e.message);
  }
}

async function moveMaterialPrompt(mid) {
  if (!subjectsCache.length) {
    alert('No subjects available.');
    return;
  }
  const list = subjectsCache.map(s => `${s.id}: ${s.name}`).join('\n');
  const selected = prompt(`Enter target subject ID:\n${list}`);
  if (!selected || !selected.trim()) return;
  const sid = Number(selected.trim());
  if (!Number.isInteger(sid)) {
    alert('Invalid subject id.');
    return;
  }
  try {
    await api(`/materials/${mid}`, { method: 'PUT', body: JSON.stringify({ subject_id: sid }) });
    if (currentSubjectId) await loadMaterialsForSubject(currentSubjectId);
    await loadSubjects();
  } catch (e) {
    alert(e.message);
  }
}
