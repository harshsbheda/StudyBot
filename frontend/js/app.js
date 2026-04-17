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
let profileCache = null;
let materialsCache = [];

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

function openGoogleHelpModal(event) {
  if (event) event.preventDefault();
  document.getElementById('googleHelpModal').classList.remove('hidden');
  return false;
}

async function login() {
  const email = document.getElementById('loginEmail').value.trim();
  const password = document.getElementById('loginPassword').value;
  const errEl = document.getElementById('loginError');
  errEl.classList.add('hidden');

  try {
    const data = await api('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
    if (data.verify_required) {
      openVerifyEmailModal(email);
      return;
    }
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
    if (data.otp_required) {
      openVerifyEmailModal(email);
      return;
    }
    setAuth(data);
    showApp();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

function googleLogin(forceChooser = false) {
  api('/auth/google-config')
    .then(cfg => {
      const clientId = cfg?.client_id || '';
      if (!clientId) {
        alert('Google login is not configured on server. Set GOOGLE_CLIENT_ID in backend/.env.');
        return;
      }
      if (!window.google?.accounts?.oauth2) {
        alert('Google Sign-In is still loading. Please wait a moment and try again.');
        return;
      }

      const tokenClient = google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope: 'openid email profile',
        prompt: forceChooser ? 'select_account' : 'select_account',
        callback: async response => {
          if (response?.error) {
            alert('Google login failed: ' + response.error);
            return;
          }
          try {
            const data = await api('/auth/google', {
              method: 'POST',
              body: JSON.stringify({ access_token: response.access_token })
            });
            setAuth(data);
            showApp();
          } catch (e) {
            alert('Google login failed: ' + e.message);
          }
        },
        error_callback: error => {
          const code = error?.type || 'unknown_error';
          if (code === 'popup_failed_to_open') {
            alert('Google popup could not open. Please allow popups for this site and try again.');
            return;
          }
          if (code === 'popup_closed') {
            alert('Google popup was closed before login finished.');
            return;
          }
          alert('Google login failed: ' + code);
        }
      });
      tokenClient.requestAccessToken({ prompt: forceChooser ? 'select_account' : 'select_account' });
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

  hydrateProfileUi(currentUser);

  initVoiceSupport();
  loadAiSettings();
  loadSubjects();
  loadChatHistory();
  loadTestHistory();
  loadProgress();
  loadDashboard();
  loadProfile();
}

function switchTab(tab) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.mobile-nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(`tab-${tab}`).classList.add('active');
  document.querySelectorAll(`[data-tab="${tab}"]`).forEach(b => b.classList.add('active'));

  if (tab === 'dashboard') loadDashboard();
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
      materialsCache = [];
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
    materialsCache = [];
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
    materialsCache = materials;
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
          <button class="mc-btn" onclick="renameMaterialPrompt(${m.id})">Rename</button>
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
    msgs.forEach(m => appendMessage(m.role, m.content, m.source, m.citations, m.model_info));
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
    appendMessage('assistant', data.answer, data.source, data.citations, data.model_info);
    await loadChatHistory();
  } catch (e) {
    typing.remove();
    appendMessage('assistant', 'Error: ' + e.message, 'ai');
  }
}

function appendMessage(role, content, source = 'ai', citations = [], modelInfo = null) {
  const el = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  let sourceLabel = source;
  if (source === 'material') sourceLabel = 'From material';
  if (source === 'external') sourceLabel = 'External knowledge';
  if (source === 'guardrail') sourceLabel = 'Limited';
  const sourceTag = source && source !== 'ai'
    ? `<span class="msg-source source-${source}">${sourceLabel}</span>`
    : '';
  const modelTag = modelInfo && modelInfo.provider
    ? `<span class="msg-source source-model">${escHtml(modelInfo.provider)}${modelInfo.model ? ' · ' + escHtml(modelInfo.model) : ''}</span>`
    : '';
  const cites = Array.isArray(citations) && citations.length
    ? `<details class="msg-citations"><summary>Sources</summary>${citations.map(c => `<div class="msg-cite">• ${escHtml(c.snippet || '')}</div>`).join('')}</details>`
    : '';

  div.innerHTML = `<div><div class="msg-bubble">${formatMarkdown(content || '')}</div>${sourceTag}${modelTag}${cites}</div>`;
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
  const feedback = Array.isArray(r.feedback) ? r.feedback : [];
  el.innerHTML = `
    <div class="result-summary">
      <div class="result-score">${r.score}%</div>
      <div class="result-grade">Grade: ${r.grade}</div>
      <div class="result-stats">${r.correct} / ${r.total} correct</div>
    </div>
    ${renderReviewFeedback(feedback)}
  `;
  document.getElementById('resultsModal').classList.remove('hidden');
  loadTestHistory();
}

async function loadTestHistory() {
  const el = document.getElementById('testHistory');
  if (!el) return;
  try {
    const rows = await api('/tests/history');
    if (!rows.length) {
      el.innerHTML = '<div class="empty-state"><p>No tests taken yet.</p></div>';
      return;
    }

    el.innerHTML = rows.map(r => `
      <div class="test-record">
        <div>
          <div class="tr-title">${escHtml(r.title)}</div>
          <div class="tr-meta">${escHtml((r.test_type || '').replace('_', ' '))} | ${r.correct_answers}/${r.total_questions} | ${formatDate(r.completed_at)}</div>
        </div>
        <div class="test-record-side">
          <div class="score-badge">${parseFloat(r.score).toFixed(1)}%</div>
          <button class="mc-btn" onclick="reviewAttempt(${r.id})">Review</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    console.error(e);
    el.innerHTML = `<div class="empty-state"><p>${escHtml(e.message || 'Could not load test history.')}</p></div>`;
  }
}

async function reviewAttempt(attemptId) {
  showLoading('Loading test review...');
  try {
    const data = await api(`/tests/attempts/${attemptId}`);
    document.getElementById('reviewTitle').textContent = data.title || 'Test Review';
    document.getElementById('reviewContent').innerHTML = `
      <div class="result-summary">
        <div class="result-score">${parseFloat(data.score || 0).toFixed(1)}%</div>
        <div class="result-grade">Grade: ${escHtml(data.grade || '-')}</div>
        <div class="result-stats">${data.correct_answers || 0} / ${data.total_questions || 0} correct</div>
      </div>
      <div class="review-meta">
        <span>${escHtml((data.test_type || '').replace('_', ' '))}</span>
        <span>${formatDuration(data.time_taken || 0)}</span>
        <span>${formatDate(data.completed_at)}</span>
      </div>
      ${renderReviewFeedback(data.feedback || [])}
    `;
    document.getElementById('reviewModal').classList.remove('hidden');
  } catch (e) {
    alert(e.message);
  } finally {
    hideLoading();
  }
}

function renderReviewFeedback(feedback) {
  if (!Array.isArray(feedback) || !feedback.length) {
    return '<div class="empty-state"><p>No review details available.</p></div>';
  }

  return feedback.map((item, index) => {
    if (item.type === 'mcq') {
      const yourAnswer = item.your_answer ? `Your answer: ${escHtml(item.your_answer)}` : 'No answer';
      const correctAnswer = `Correct answer: ${escHtml(item.correct_answer || '-')}`;
      const explanation = item.explanation ? `<div class="fb-explanation">${escHtml(item.explanation)}</div>` : '';
      return `
        <div class="feedback-item">
          <div class="fb-question">Q${index + 1}. ${escHtml(item.question || '')}</div>
          <div class="${item.correct ? 'fb-correct' : 'fb-wrong'}">${item.correct ? 'Correct' : 'Incorrect'}</div>
          <div class="fb-detail">${yourAnswer}</div>
          ${!item.correct ? `<div class="fb-detail">${correctAnswer}</div>${explanation}` : ''}
        </div>
      `;
    }

    const score = Number(item.score || 0);
    const statusClass = score >= 6 ? 'fb-correct' : 'fb-wrong';
    const missed = Array.isArray(item.missed) && item.missed.length
      ? `<div class="fb-explanation">Missed points: ${escHtml(item.missed.join(', '))}</div>`
      : '';
    return `
      <div class="feedback-item">
        <div class="fb-question">Q${index + 1}. ${escHtml(item.question || '')}</div>
        <div class="${statusClass}">Score: ${score}/10</div>
        <div class="fb-detail">Your answer: ${escHtml(item.your_answer || 'No answer')}</div>
        <div class="fb-detail">Expected answer: ${escHtml(item.model_answer || '-')}</div>
        <div class="fb-explanation">${escHtml(item.feedback || '')}</div>
        ${missed}
      </div>
    `;
  }).join('');
}

async function loadDashboard() {
  const el = document.getElementById('dashboardGrid');
  if (!el) return;

  try {
    const [progress, subjects, sessions, tests] = await Promise.all([
      api('/progress/'),
      api('/materials/subjects'),
      api('/chat/sessions'),
      api('/tests/history'),
    ]);

    const p = progress.progress || {};
    const subjectCount = Array.isArray(subjects) ? subjects.length : 0;
    const materialCount = Array.isArray(subjects)
      ? subjects.reduce((sum, s) => sum + (s.material_count || 0), 0)
      : 0;
    const sessionCount = Array.isArray(sessions) ? sessions.length : 0;
    const recentSessions = (sessions || []).slice(0, 3);
    const recentTests = (tests || []).slice(0, 3);
    const streak = p.study_streak || 0;
    const lastStudy = p.last_study_date ? String(p.last_study_date).slice(0, 10) : '-';
    const activeSubject = (subjects || []).find(s => Number(s.id) === Number(currentSubjectId));
    const aiMode = getAiModeLabel();
    const voiceReady = Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
    const refreshAt = formatTime(new Date());

    el.innerHTML = `
      <div class="dash-card dash-hero">
        <div>
          <div class="dash-kicker">Welcome back</div>
          <div class="dash-title">${escHtml(currentUser?.name || 'Student')}</div>
          <div class="dash-subtitle">Let’s keep the momentum going today.</div>
        </div>
        <div class="dash-actions">
          <button class="btn-primary-sm" onclick="switchTab('chat')">Ask a question</button>
          <button class="btn-secondary" onclick="switchTab('materials')">Upload notes</button>
        </div>
      </div>

      <div class="dash-card">
        <div class="dash-label">Subjects</div>
        <div class="dash-metric">${subjectCount}</div>
        <div class="dash-note">Total study areas</div>
      </div>
      <div class="dash-card">
        <div class="dash-label">Study Streak</div>
        <div class="dash-metric">${streak}🔥</div>
        <div class="dash-note">Last study: ${escHtml(lastStudy)}</div>
      </div>
      <div class="dash-card">
        <div class="dash-label">Materials</div>
        <div class="dash-metric">${materialCount}</div>
        <div class="dash-note">Files uploaded</div>
      </div>
      <div class="dash-card">
        <div class="dash-label">Tests</div>
        <div class="dash-metric">${p.total_tests || 0}</div>
        <div class="dash-note">Average ${p.avg_score ? parseFloat(p.avg_score).toFixed(1) + '%' : '-'}</div>
      </div>
      <div class="dash-card">
        <div class="dash-label">Chat Sessions</div>
        <div class="dash-metric">${sessionCount}</div>
        <div class="dash-note">Conversations saved</div>
      </div>

      <div class="dash-card dash-status">
        <div class="dash-label">Live System Status</div>
        <div class="dash-status-grid">
          <div class="status-chip status-online">
            <span>API Link</span>
            <strong>Live and synced</strong>
          </div>
          <div class="status-chip status-ready">
            <span>AI Routing</span>
            <strong>${escHtml(aiMode)}</strong>
          </div>
          <div class="status-chip ${activeSubject ? 'status-ready' : 'status-warn'}">
            <span>Focus Subject</span>
            <strong>${escHtml(activeSubject?.name || 'Select a subject')}</strong>
          </div>
          <div class="status-chip ${voiceReady ? 'status-online' : 'status-warn'}">
            <span>Voice Input</span>
            <strong>${voiceReady ? 'Available' : 'Not supported here'}</strong>
          </div>
        </div>
        <div class="status-meta">
          Knowledge base: ${materialCount} file${materialCount === 1 ? '' : 's'} across ${subjectCount} subject${subjectCount === 1 ? '' : 's'}
          · Last sync ${escHtml(refreshAt)}
        </div>
      </div>

      <div class="dash-card dash-list">
        <div class="dash-label">Recent Chats</div>
        ${recentSessions.length ? `
          <ul class="dash-list-items">
            ${recentSessions.map(s => `<li>${escHtml(s.session_name || 'Chat session')}</li>`).join('')}
          </ul>
        ` : `<div class="dash-empty">No recent chats yet.</div>`}
      </div>

      <div class="dash-card dash-list">
        <div class="dash-label">Recent Tests</div>
        ${recentTests.length ? `
          <ul class="dash-list-items">
            ${recentTests.map(t => `<li>${escHtml(t.title || 'Test')} · ${parseFloat(t.score || 0).toFixed(1)}%</li>`).join('')}
          </ul>
        ` : `<div class="dash-empty">No tests taken yet.</div>`}
      </div>
    `;
  } catch (e) {
    el.innerHTML = `<div class="empty-state"><p>${escHtml(e.message || 'Could not load dashboard.')}</p></div>`;
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

function openVerifyEmailModal(email) {
  document.getElementById('verifyEmail').value = email || '';
  document.getElementById('verifyOtp').value = '';
  document.getElementById('verifyError').classList.add('hidden');
  document.getElementById('verifyEmailModal').classList.remove('hidden');
}

async function verifyEmailOtp() {
  const email = document.getElementById('verifyEmail').value.trim();
  const otp = document.getElementById('verifyOtp').value.trim();
  const errEl = document.getElementById('verifyError');
  errEl.classList.add('hidden');

  try {
    const data = await api('/auth/verify-email', {
      method: 'POST',
      body: JSON.stringify({ email, otp })
    });
    if (data.success) {
      closeModal('verifyEmailModal');
      alert('Email verified. Please sign in.');
      showLogin();
    }
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

async function resendVerifyOtp() {
  const email = document.getElementById('verifyEmail').value.trim();
  const errEl = document.getElementById('verifyError');
  errEl.classList.add('hidden');
  try {
    const data = await api('/auth/resend-verification', {
      method: 'POST',
      body: JSON.stringify({ email })
    });
    alert(data.message || 'OTP sent.');
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

function openResetModal() {
  document.getElementById('resetEmail').value = '';
  document.getElementById('resetToken').value = '';
  document.getElementById('resetPassword').value = '';
  document.getElementById('resetTokenHint').textContent = '';
  document.getElementById('resetError').classList.add('hidden');
  document.getElementById('resetModal').classList.remove('hidden');
}

async function requestResetCode() {
  const email = document.getElementById('resetEmail').value.trim();
  const errEl = document.getElementById('resetError');
  errEl.classList.add('hidden');
  try {
    const res = await fetch(`${API}/auth/forgot-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    const hint = document.getElementById('resetTokenHint');
    hint.textContent = res.message || 'If the email exists, an OTP was sent.';
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

async function resetPassword() {
  const email = document.getElementById('resetEmail').value.trim();
  const token = document.getElementById('resetToken').value.trim();
  const newPassword = document.getElementById('resetPassword').value;
  const errEl = document.getElementById('resetError');
  errEl.classList.add('hidden');
  try {
    const res = await fetch(`${API}/auth/reset-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, token, new_password: newPassword })
    }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    closeModal('resetModal');
    alert('Password reset successful. Please sign in.');
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

function hydrateProfileUi(user) {
  if (!user) return;
  const name = user.name || 'Student';
  const email = user.email || '';
  document.getElementById('userNameSb').textContent = name;
  document.getElementById('userEmailSb').textContent = email;
  const avatar = document.getElementById('userAvatarSb');
  if (user.avatar_url) {
    avatar.textContent = '';
    avatar.style.backgroundImage = `url('${user.avatar_url}')`;
    avatar.style.backgroundSize = 'cover';
    avatar.style.backgroundPosition = 'center';
  } else {
    avatar.style.backgroundImage = '';
    avatar.textContent = (name[0] || 'S').toUpperCase();
  }
}

function openProfileModal() {
  const u = profileCache || currentUser || {};
  document.getElementById('profileName').value = u.name || '';
  document.getElementById('profileEmail').value = u.email || '';
  document.getElementById('profilePhone').value = u.phone || '';
  document.getElementById('profileAvatar').value = u.avatar_url || '';
  document.getElementById('profileBio').value = u.bio || '';
  document.getElementById('profileError').classList.add('hidden');
  document.getElementById('profileModal').classList.remove('hidden');
}

async function loadProfile() {
  try {
    const data = await api('/auth/profile');
    profileCache = data;
    currentUser = { ...currentUser, ...data };
    localStorage.setItem('sb_user', JSON.stringify(currentUser));
    hydrateProfileUi(currentUser);
  } catch (e) {
    console.error('Profile load failed:', e);
  }
}

async function saveProfile() {
  const name = document.getElementById('profileName').value.trim();
  const email = document.getElementById('profileEmail').value.trim();
  const phone = document.getElementById('profilePhone').value.trim();
  const avatar_url = document.getElementById('profileAvatar').value.trim();
  const bio = document.getElementById('profileBio').value.trim();
  const errEl = document.getElementById('profileError');
  errEl.classList.add('hidden');

  try {
    const res = await api('/auth/profile', {
      method: 'PUT',
      body: JSON.stringify({ name, email, phone, avatar_url, bio })
    });
    if (res.user) {
      currentUser = { ...currentUser, ...res.user };
      localStorage.setItem('sb_user', JSON.stringify(currentUser));
      hydrateProfileUi(currentUser);
    }
    closeModal('profileModal');
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
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

document.addEventListener('DOMContentLoaded', () => {
  const avatar = document.getElementById('userAvatarSb');
  if (avatar) {
    avatar.style.cursor = 'pointer';
    avatar.title = 'Edit profile';
    avatar.addEventListener('click', openProfileModal);
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

function formatDuration(seconds) {
  const total = Number(seconds || 0);
  if (!total) return '-';
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  if (!mins) return `${secs}s`;
  return `${mins}m ${secs}s`;
}

function formatTime(dt) {
  if (!dt) return '-';
  const date = new Date(dt);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}

function formatDate(dt) {
  if (!dt) return '-';
  const date = new Date(dt);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function getAiModeLabel() {
  const provider = document.getElementById('aiProviderSelect')?.value || aiSettings?.provider || 'auto';
  const selectedModel = document.getElementById('aiModelSelect')?.value || '';

  if (provider === 'auto') {
    return selectedModel ? `Auto · ${selectedModel}` : 'Auto routing';
  }

  const label = provider === 'openai'
    ? 'OpenAI'
    : provider === 'gemini'
      ? 'Gemini'
      : provider;

  return selectedModel ? `${label} · ${selectedModel}` : label;
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

async function renameMaterialPrompt(mid) {
  const material = materialsCache.find(m => Number(m.id) === Number(mid));
  const currentTitle = material?.title || '';
  const next = prompt('New file title:', currentTitle);
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
