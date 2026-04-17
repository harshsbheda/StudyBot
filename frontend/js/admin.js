const API = window.STUDYBOT_API_BASE || 'http://localhost:5000/api';
let adminToken = localStorage.getItem('sb_admin_token');
let allUsers = [];
let allMaterials = [];

function getHeaders(json = true) {
  const headers = {};
  if (json) headers['Content-Type'] = 'application/json';
  if (adminToken) headers['Authorization'] = `Bearer ${adminToken}`;
  return headers;
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: { ...getHeaders(true), ...(options.headers || {}) }
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

async function apiBlob(path) {
  const res = await fetch(`${API}${path}`, {
    method: 'GET',
    headers: getHeaders(false)
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Download failed');
  }

  return res.blob();
}

document.addEventListener('DOMContentLoaded', () => {
  if (adminToken) {
    showAdminApp();
  } else {
    document.getElementById('adminAuth').classList.remove('hidden');
  }
});

async function adminLogin() {
  const email = document.getElementById('adminEmail').value.trim();
  const password = document.getElementById('adminPassword').value;
  const errEl = document.getElementById('adminLoginError');
  errEl.classList.add('hidden');

  try {
    const data = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    }).then(r => r.json());

    if (data.error) throw new Error(data.error);
    if (data.user?.role !== 'admin') throw new Error('Access denied. Admin accounts only.');

    adminToken = data.token;
    localStorage.setItem('sb_admin_token', adminToken);
    document.getElementById('adminUserName').textContent = `Logged in as: ${data.user.name}`;
    showAdminApp();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

function adminLogout() {
  adminToken = null;
  localStorage.removeItem('sb_admin_token');
  location.reload();
}

function showAdminApp() {
  document.getElementById('adminAuth').classList.add('hidden');
  document.getElementById('adminApp').classList.remove('hidden');
  loadDashboard();
  loadUsers();
  loadAdminMaterials();
  loadAdminTests();
  loadSubjectAnalytics();
  loadReports();
  loadGoogleOAuthCheck();
}

function adminTab(tab, btn) {
  document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.admin-nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(`admin-tab-${tab}`).classList.add('active');
  btn.classList.add('active');
  if (tab === 'reports') loadReports();
}

async function loadDashboard() {
  try {
    const stats = await api('/admin/stats');
    const syncAt = formatTime(new Date());
    document.getElementById('dashboardStats').innerHTML = `
      <div class="admin-stat green">
        <div class="as-icon">U</div>
        <div class="as-value">${stats.total_users}</div>
        <div class="as-label">Total Students</div>
      </div>
      <div class="admin-stat blue">
        <div class="as-icon">M</div>
        <div class="as-value">${stats.total_materials}</div>
        <div class="as-label">Materials Uploaded</div>
      </div>
      <div class="admin-stat amber">
        <div class="as-icon">T</div>
        <div class="as-value">${stats.total_tests_taken}</div>
        <div class="as-label">Tests Taken</div>
      </div>
      <div class="admin-stat">
        <div class="as-icon">S</div>
        <div class="as-value">${stats.platform_avg_score}%</div>
        <div class="as-label">Platform Avg Score</div>
      </div>
      <div class="admin-stat blue">
        <div class="as-icon">IO</div>
        <div class="as-value">LIVE</div>
        <div class="as-label">System Sync - ${syncAt}</div>
      </div>`;
  } catch (e) {
    document.getElementById('dashboardStats').innerHTML = `<p style="color:var(--red);padding:20px">Error: ${e.message}</p>`;
  }
}

async function loadUsers() {
  try {
    allUsers = await api('/admin/users');
    renderUsers(allUsers);
  } catch (e) {
    document.getElementById('usersBody').innerHTML = `<tr><td colspan="10">${e.message}</td></tr>`;
  }
}

function renderUsers(users) {
  const tbody = document.getElementById('usersBody');
  if (!users.length) {
    tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--text-faint)">No users found</td></tr>`;
    return;
  }

  tbody.innerHTML = users.map(u => `
    <tr>
      <td>${u.id}</td>
      <td><strong style="color:var(--text)">${escHtml(u.name)}</strong></td>
      <td>${escHtml(u.email)}</td>
      <td><span class="badge badge-${u.role}">${u.role}</span></td>
      <td>${u.total_tests || 0}</td>
      <td class="${scoreClass(u.avg_score)}">${u.avg_score ? parseFloat(u.avg_score).toFixed(1) + '%' : '-'}</td>
      <td>${u.materials_uploaded || 0}</td>
      <td><span class="badge ${u.is_active ? 'badge-active' : 'badge-suspended'}">${u.is_active ? 'Active' : 'Suspended'}</span></td>
      <td>${formatDate(u.created_at)}</td>
      <td>
        <div class="tbl-actions">
          <button class="tbl-btn" onclick="openEditUser(${u.id}, '${u.role}', ${u.is_active})">Edit</button>
          <button class="tbl-btn" onclick="viewUserProfile(${u.id})">Profile</button>
          <button class="tbl-btn" onclick="resetUserPassword(${u.id})">Reset Password</button>${u.role !== 'admin' ? `<button class="tbl-btn danger" onclick="deleteUser(${u.id})">Delete</button>` : ''}
        </div>
      </td>
    </tr>`).join('');
}

function filterUsers(q) {
  const query = (q || '').toLowerCase();
  renderUsers(allUsers.filter(u => u.name.toLowerCase().includes(query) || u.email.toLowerCase().includes(query)));
}

function openCreateUserModal() {
  document.getElementById('newUserName').value = '';
  document.getElementById('newUserEmail').value = '';
  document.getElementById('newUserPassword').value = '';
  document.getElementById('newUserRole').value = 'student';
  document.getElementById('newUserActive').value = '1';
  document.getElementById('newUserError').classList.add('hidden');
  document.getElementById('createUserModal').classList.remove('hidden');
}

async function createUser() {
  const name = document.getElementById('newUserName').value.trim();
  const email = document.getElementById('newUserEmail').value.trim();
  const password = document.getElementById('newUserPassword').value;
  const role = document.getElementById('newUserRole').value;
  const isActive = document.getElementById('newUserActive').value === '1';
  const errEl = document.getElementById('newUserError');

  errEl.classList.add('hidden');
  try {
    await api('/admin/users', {
      method: 'POST',
      body: JSON.stringify({ name, email, password, role, is_active: isActive })
    });
    closeModal('createUserModal');
    loadUsers();
    loadDashboard();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

function openEditUser(id, role, active) {
  document.getElementById('editUserId').value = id;
  document.getElementById('editUserRole').value = role;
  document.getElementById('editUserActive').value = active ? '1' : '0';
  document.getElementById('editUserModal').classList.remove('hidden');
}

async function saveUser() {
  const id = document.getElementById('editUserId').value;
  const role = document.getElementById('editUserRole').value;
  const active = document.getElementById('editUserActive').value === '1';
  try {
    await api(`/admin/users/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ role, is_active: active })
    });
    closeModal('editUserModal');
    loadUsers();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function deleteUser(id) {
  if (!confirm('Permanently delete this user and all their data?')) return;
  try {
    await api(`/admin/users/${id}`, { method: 'DELETE' });
    loadUsers();
    loadDashboard();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function loadAdminMaterials() {
  try {
    allMaterials = await api('/admin/materials');
    renderMaterials(allMaterials);
  } catch (e) {
    document.getElementById('materialsBody').innerHTML = `<tr><td colspan="8">${e.message}</td></tr>`;
  }
}

function renderMaterials(materials) {
  const tbody = document.getElementById('materialsBody');
  if (!materials.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:20px;color:var(--text-faint)">No materials found</td></tr>`;
    return;
  }

  tbody.innerHTML = materials.map(m => `
    <tr>
      <td>${m.id}</td>
      <td><strong style="color:var(--text)">${escHtml(m.title)}</strong></td>
      <td>${escHtml(m.subject || '-')}</td>
      <td>${escHtml(m.user_name || '-')}</td>
      <td><span class="badge badge-student">${m.file_type}</span></td>
      <td>${formatSize(m.file_size)}</td>
      <td>${formatDate(m.created_at)}</td>
      <td>
        <div class="tbl-actions">
          <button class="tbl-btn" onclick="viewMaterial(${m.id})">View</button>
          <button class="tbl-btn" onclick="downloadMaterial(${m.id}, '${escJs(m.filename || 'material')}')">Download</button>
          <button class="tbl-btn danger" onclick="adminDeleteMaterial(${m.id})">Delete</button>
        </div>
      </td>
    </tr>`).join('');
}

function filterMaterials(q) {
  const query = (q || '').toLowerCase();
  renderMaterials(allMaterials.filter(m =>
    (m.title || '').toLowerCase().includes(query) ||
    (m.subject || '').toLowerCase().includes(query) ||
    (m.user_name || '').toLowerCase().includes(query)
  ));
}

async function viewMaterial(id) {
  try {
    const m = await api(`/admin/materials/${id}/content`);
    const topics = Array.isArray(m.key_topics) ? m.key_topics : [];
    document.getElementById('materialPreviewTitle').textContent = m.title || 'Material';
    document.getElementById('materialPreviewMeta').textContent = `${m.user_name || ''} (${m.user_email || ''}) | ${m.file_type || ''} | ${formatSize(m.file_size)}`;
    document.getElementById('materialPreviewTopics').innerHTML = topics.length
      ? topics.map(t => `<span class="badge badge-student" style="margin-right:6px">${escHtml(t)}</span>`).join('')
      : '<span style="color:var(--text-faint)">No topics extracted</span>';
    document.getElementById('materialPreviewText').textContent = m.extracted_text || 'No extracted text available.';
    document.getElementById('materialPreviewModal').classList.remove('hidden');
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function downloadMaterial(id, fallbackName) {
  try {
    const blob = await apiBlob(`/admin/materials/${id}/download`);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fallbackName || `material_${id}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert(`Download failed: ${e.message}`);
  }
}

async function adminDeleteMaterial(id) {
  if (!confirm('Delete this material?')) return;
  try {
    await api(`/admin/materials/${id}`, { method: 'DELETE' });
    loadAdminMaterials();
    loadDashboard();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function loadAdminTests() {
  try {
    const data = await api('/admin/tests');
    const tbody = document.getElementById('testsBody');
    if (!data.length) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:20px;color:var(--text-faint)">No test attempts yet</td></tr>`;
      return;
    }
    tbody.innerHTML = data.map(t => {
      const sc = parseFloat(t.score || 0);
      return `<tr>
        <td>${t.id}</td>
        <td><strong style="color:var(--text)">${escHtml(t.title || '-')}</strong></td>
        <td>${escHtml(t.student_name || '-')}</td>
        <td>${(t.test_type || '').replace('_', ' ')}</td>
        <td class="${scoreClass(sc)}">${sc.toFixed(1)}%</td>
        <td>${t.correct_answers}/${t.total_questions}</td>
        <td>${t.time_taken ? Math.floor(t.time_taken / 60) + 'm ' + (t.time_taken % 60) + 's' : '-'}</td>
        <td>${formatDate(t.completed_at)}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    document.getElementById('testsBody').innerHTML = `<tr><td colspan="8">${e.message}</td></tr>`;
  }
}

async function loadSubjectAnalytics() {
  const tbody = document.getElementById('subjectAnalyticsBody');
  if (!tbody) return;
  try {
    const rows = await api('/admin/subjects/analytics');
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:20px;color:var(--text-faint)">No analytics data yet</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${escHtml(r.user_name || '-')}</td>
        <td>${escHtml(r.subject_name || 'General')}</td>
        <td>${r.materials || 0}</td>
        <td>${r.attempts || 0}</td>
        <td class="${scoreClass(r.avg_score)}">${parseFloat(r.avg_score || 0).toFixed(1)}%</td>
      </tr>`).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--red);text-align:center;padding:20px">${e.message}</td></tr>`;
  }
}

async function loadGoogleOAuthCheck() {
  const el = document.getElementById('googleOAuthCheckCard');
  if (!el) return;

  try {
    const data = await api('/admin/oauth/google-check');
    const checks = (data.checks || [])
      .map(c => `<div class="oauth-check ${c.ok ? 'ok' : 'bad'}">${c.ok ? 'OK' : 'FIX'} ${escHtml(c.message || '')}</div>`)
      .join('');

    const suggestedOrigins = (data.suggested_origins || [])
      .map(item => `<code>${escHtml(item)}</code>`)
      .join('<br>');
    const suggestedRedirects = (data.suggested_redirects || [])
      .map(item => `<code>${escHtml(item)}</code>`)
      .join('<br>');

    el.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
        <h4 style="margin:0">Config Status: ${data.healthy ? '<span class="score-high">Healthy</span>' : '<span class="score-low">Needs Fix</span>'}</h4>
        <a class="tbl-btn" href="${escHtml(data.google_console_credentials_url || '#')}" target="_blank" rel="noopener">Open Google Console</a>
      </div>
      <p style="margin-top:10px;color:var(--text-muted)">Detected Origin: <code>${escHtml(data.detected_origin || '-')}</code></p>
      <div class="oauth-check-list">${checks || '<p style="color:var(--text-faint)">No checks available.</p>'}</div>
      <div class="oauth-grid">
        <div>
          <p style="font-weight:600;margin-bottom:6px">Suggested Authorized JavaScript Origins</p>
          ${suggestedOrigins || '<p style="color:var(--text-faint)">None</p>'}
        </div>
        <div>
          <p style="font-weight:600;margin-bottom:6px">Suggested Redirect URIs</p>
          ${suggestedRedirects || '<p style="color:var(--text-faint)">None</p>'}
        </div>
      </div>
      <p style="margin-top:10px;color:var(--text-faint)">${escHtml(data.note || '')}</p>
    `;
  } catch (e) {
    el.innerHTML = `<p style="color:var(--red)">Error loading OAuth check: ${escHtml(e.message)}</p>`;
  }
}

async function loadReports() {
  try {
    const range = getReportRangeQuery();
    const [overview, insights] = await Promise.all([
      api(`/admin/reports/overview${range}`),
      api(`/admin/users/insights${range}`),
    ]);

    const totals = overview.totals || {};
    const activity = overview.activity || {};

    document.getElementById('reportsOverview').innerHTML = `
      <div class="admin-stat green">
        <div class="as-icon">U</div>
        <div class="as-value">${totals.total_users || 0}</div>
        <div class="as-label">Total Students</div>
      </div>
      <div class="admin-stat blue">
        <div class="as-icon">A</div>
        <div class="as-value">${activity.active_7d || 0}</div>
        <div class="as-label">Active (7 days)</div>
      </div>
      <div class="admin-stat amber">
        <div class="as-icon">M</div>
        <div class="as-value">${totals.total_materials || 0}</div>
        <div class="as-label">Materials</div>
      </div>
      <div class="admin-stat">
        <div class="as-icon">T</div>
        <div class="as-value">${totals.total_tests_taken || 0}</div>
        <div class="as-label">Tests Taken</div>
      </div>
      <div class="admin-stat">
        <div class="as-icon">C</div>
        <div class="as-value">${totals.total_chat_sessions || 0}</div>
        <div class="as-label">Chat Sessions</div>
      </div>
      <div class="admin-stat">
        <div class="as-icon">P</div>
        <div class="as-value">${activity.avg_materials_per_user || 0}</div>
        <div class="as-label">Avg Materials/User</div>
      </div>
    `;

    const topSubjectsBody = document.getElementById('reportsTopSubjects');
    const topSubjects = overview.top_subjects || [];
    topSubjectsBody.innerHTML = topSubjects.length
      ? topSubjects.map(s => `<tr><td>${escHtml(s.subject)}</td><td>${s.materials || 0}</td><td>${s.users || 0}</td></tr>`).join('')
      : `<tr><td colspan="3" style="text-align:center;padding:12px;color:var(--text-faint)">No subject data yet</td></tr>`;

    const activityEl = document.getElementById('reportsActivity');
    const uploads = overview.uploads_timeline || [];
    const tests = overview.tests_timeline || [];
    const newUsers = overview.new_users_timeline || [];
    activityEl.innerHTML = `
      <div class="report-list">
        <div>
          <h5>Uploads</h5>
          ${uploads.length ? uploads.map(r => `<div>${r.day}: ${r.count}</div>`).join('') : '<div class="muted">No uploads</div>'}
        </div>
        <div>
          <h5>Tests</h5>
          ${tests.length ? tests.map(r => `<div>${r.day}: ${r.count} (avg ${parseFloat(r.avg_score || 0).toFixed(1)}%)</div>`).join('') : '<div class="muted">No tests</div>'}
        </div>
        <div>
          <h5>New Users</h5>
          ${newUsers.length ? newUsers.map(r => `<div>${r.day}: ${r.count}</div>`).join('') : '<div class="muted">No signups</div>'}
        </div>
      </div>
    `;

    const insightsEl = document.getElementById('reportsInsights');
    insightsEl.innerHTML = `
      <div class="report-split">
        ${renderInsightTable('Top Materials', insights.top_materials || [])}
        ${renderInsightTable('Top Tests', insights.top_tests || [])}
        ${renderInsightTable('Top Scores', insights.top_scores || [])}
      </div>
    `;

    const inactiveEl = document.getElementById('reportsInactive');
    inactiveEl.innerHTML = `
      <div class="report-split">
        ${renderInsightTable('Inactive 7d', insights.inactive_7d || [])}
        ${renderInsightTable('Inactive 30d', insights.inactive_30d || [])}
      </div>
    `;
  } catch (e) {
    document.getElementById('reportsOverview').innerHTML = `<p style="color:var(--red);padding:20px">Error: ${escHtml(e.message)}</p>`;
  }
}

function renderInsightTable(title, rows) {
  const body = rows.length
    ? rows.map(r => `<tr><td>${escHtml(r.name || '-')}</td><td>${r.materials || 0}</td><td>${r.tests || 0}</td><td>${parseFloat(r.avg_score || 0).toFixed(1)}%</td></tr>`).join('')
    : `<tr><td colspan="4" style="text-align:center;padding:8px;color:var(--text-faint)">No data</td></tr>`;

  return `
    <div>
      <h5>${escHtml(title)}</h5>
      <table class="admin-table compact">
        <thead><tr><th>User</th><th>Mat</th><th>Tests</th><th>Avg</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

async function exportData(kind) {
  try {
    const range = getReportRangeQuery();
    const ids = (document.getElementById('exportUserIds')?.value || '').trim();
    const userIds = ids ? `&user_ids=${encodeURIComponent(ids)}` : '';
    const blob = await apiBlob(`/admin/export/${kind}${range}${userIds}`);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `studybot_${kind}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert(`Export failed: ${e.message}`);
  }
}

function getReportRangeQuery() {
  const start = document.getElementById('reportStart')?.value || '';
  const end = document.getElementById('reportEnd')?.value || '';
  const params = new URLSearchParams();
  if (start) params.set('start_date', start);
  if (end) params.set('end_date', end);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

function applyReportFilter() {
  loadReports();
}

function clearReportFilter() {
  const s = document.getElementById('reportStart');
  const e = document.getElementById('reportEnd');
  if (s) s.value = '';
  if (e) e.value = '';
  loadReports();
}

async function viewUserProfile(id) {
  try {
    const data = await api(`/admin/users/${id}/profile`);
    const p = data.profile || {};
    const mats = data.recent_materials || [];
    const tests = data.recent_tests || [];
    const uploadsTl = data.uploads_timeline || [];
    const testsTl = data.tests_timeline || [];

    document.getElementById('userProfileTitle').textContent = `${p.name || 'User'} (${p.email || ''})`;
    const el = document.getElementById('userProfileContent');
    el.innerHTML = `
      <div class="admin-card" style="margin-bottom:12px">
        <p><strong>Role:</strong> ${escHtml(p.role || '-')}</p>
        <p><strong>Status:</strong> ${p.is_active ? 'Active' : 'Suspended'}</p>
        <p><strong>Joined:</strong> ${formatDate(p.created_at)}</p>
        <p><strong>Last login:</strong> ${formatDate(p.last_login)}</p>
        <p><strong>Materials:</strong> ${p.materials_uploaded || 0} | <strong>Tests:</strong> ${p.total_tests || 0} | <strong>Avg:</strong> ${parseFloat(p.avg_score || 0).toFixed(1)}%</p>
      </div>
      <div class="admin-card" style="margin-bottom:12px">
        <h4>Recent Materials</h4>
        ${mats.length ? mats.map(m => `<div style="padding:6px 0;border-bottom:1px solid var(--border-soft)">${escHtml(m.title)} <span style="color:var(--text-faint)">(${escHtml(m.subject || '-')})</span></div>`).join('') : '<p style="color:var(--text-faint)">No materials</p>'}
      </div>
      <div class="admin-card" style="margin-bottom:12px">
        <h4>Recent Tests</h4>
        ${tests.length ? tests.map(t => `<div style="padding:6px 0;border-bottom:1px solid var(--border-soft)">${escHtml(t.title)} - <strong>${parseFloat(t.score || 0).toFixed(1)}%</strong></div>`).join('') : '<p style="color:var(--text-faint)">No tests</p>'}
      </div>
      <div class="admin-card">
        <h4>Activity Timeline (14 days)</h4>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div>
            <p style="font-weight:600;margin-bottom:6px">Uploads</p>
            ${uploadsTl.length ? uploadsTl.map(r => `<div style="font-size:0.82rem;color:var(--text-muted)">${r.day}: ${r.uploaded}</div>`).join('') : '<p style="color:var(--text-faint)">No upload activity</p>'}
          </div>
          <div>
            <p style="font-weight:600;margin-bottom:6px">Test Attempts</p>
            ${testsTl.length ? testsTl.map(r => `<div style="font-size:0.82rem;color:var(--text-muted)">${r.day}: ${r.attempts} attempt(s), avg ${parseFloat(r.avg_score || 0).toFixed(1)}%</div>`).join('') : '<p style="color:var(--text-faint)">No test activity</p>'}
          </div>
        </div>
      </div>
    `;
    document.getElementById('userProfileModal').classList.remove('hidden');
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

function scoreClass(sc) {
  if (sc >= 75) return 'score-high';
  if (sc >= 50) return 'score-mid';
  return 'score-low';
}

function formatDate(dt) {
  if (!dt) return '-';
  return new Date(dt).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function formatTime(dt) {
  if (!dt) return '-';
  return new Date(dt).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
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

function escJs(s) {
  return String(s || '').replace(/'/g, "\\'");
}

function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
}

document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.add('hidden');
  }
});

async function resetUserPassword(id) {
  const newPassword = prompt('Enter new password (min 6 chars):');
  if (!newPassword) return;
  if (newPassword.length < 6) {
    alert('Password must be at least 6 characters.');
    return;
  }

  try {
    await api(`/admin/users/${id}/reset-password`, {
      method: 'POST',
      body: JSON.stringify({ new_password: newPassword })
    });
    alert('Password reset successful.');
  } catch (e) {
    alert(`Error: ${e.message}`);
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
