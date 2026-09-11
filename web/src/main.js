import { computed, createApp, onMounted, onUnmounted, ref } from 'vue'
import './style.css'

const stateLabels = {
  awaiting_approval: '等待审批', completed: '已完成', executing: '执行中',
  rejected: '已拒绝', failed_recoverable: '可恢复失败', failed_terminal: '终止失败',
  pending: '待审批', approved: '已批准', consumed: '已消费', succeeded: '已写入',
  unknown_outcome: '结果未知', cancelled: '已取消', expired: '已过期',
}
const navItems = [
  { id: 'home', label: '总览', hint: '工作区概览' },
  { id: 'triage', label: 'Triage 工作台', hint: '从输入到审批' },
  { id: 'evidence', label: '证据浏览器', hint: '查看可追溯依据' },
  { id: 'activity', label: '运行历史', hint: '回看执行记录' },
  { id: 'audio', label: '语音实验', hint: 'ASR 与 TTS' },
]

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`)
  return payload
}

createApp({
  setup() {
    const route = ref(window.location.hash.slice(2) || 'home')
    const health = ref('检查中')
    const busy = ref(false)
    const busyAction = ref('')
    const path = ref('fixtures/transcripts/bug-triage-redacted-v1.json')
    const evidencePath = ref('README.md')
    const approver = ref('human-web')
    const speech = ref('准备提交任务')
    const runs = ref([])
    const current = ref(null)
    const evidenceResult = ref(null)
    const audioUrl = ref('')
    const error = ref('')
    const notice = ref('')
    const fileInput = ref(null)

    const page = computed(() => navItems.find((item) => item.id === route.value) || navItems[0])
    const candidates = computed(() => current.value?.candidates || [])
    const executionPlan = computed(() => current.value?.execution_plan || null)
    const receipt = computed(() => current.value?.receipt || null)
    const trace = computed(() => current.value?.trace_summary || null)
    const checkpoints = computed(() => current.value?.checkpoint_summary || null)
    const state = computed(() => current.value?.state || '')
    const canApprove = computed(() => state.value === 'awaiting_approval')
    const canRecover = computed(() => receipt.value?.status === 'unknown_outcome')
    const completedRuns = computed(() => runs.value.filter((item) => item.state === 'completed').length)
    const pendingRuns = computed(() => runs.value.filter((item) => item.state === 'awaiting_approval').length)

    const navigate = (next) => { window.location.hash = `/${next}` }
    const syncRoute = () => {
      const next = window.location.hash.slice(2) || 'home'
      route.value = navItems.some((item) => item.id === next) ? next : 'home'
    }
    const clearFeedback = () => { error.value = ''; notice.value = '' }
    const publish = (result, message = '') => { current.value = result; notice.value = message; error.value = '' }
    const labelState = (value) => stateLabels[value] || value || '未创建'
    const stateClass = (value) => `state state-${value || 'empty'}`
    const formatValue = (value) => {
      if (value === null || value === undefined || value === '') return '未设置'
      return Array.isArray(value) ? value.join('、') || '未设置' : String(value)
    }
    const formatDate = (value) => (value ? String(value).replace('T', ' ').slice(0, 16) : '未记录')

    const loadRuns = async () => {
      try { runs.value = (await requestJson('/api/runs')).runs || [] } catch (caught) { error.value = caught.message }
    }
    const selectRun = async (sessionId) => {
      busyAction.value = `history-${sessionId}`; clearFeedback()
      try { publish(await requestJson(`/api/runs/${encodeURIComponent(sessionId)}`), '已加载运行记录'); navigate('triage') }
      catch (caught) { error.value = caught.message } finally { busyAction.value = '' }
    }
    const run = async () => {
      busy.value = true; clearFeedback()
      try {
        const file = fileInput.value?.files?.[0]
        const result = file
          ? await requestJson('/api/run', { method: 'POST', body: (() => { const form = new FormData(); form.append('file', file); return form })() })
          : await requestJson('/api/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ path: path.value }) })
        publish(result, '已生成待审批任务'); navigate('triage'); await loadRuns()
      } catch (caught) { error.value = caught.message } finally { busy.value = false }
    }
    const approve = async (approved) => {
      if (!current.value) return
      busyAction.value = approved ? 'approve' : 'reject'; clearFeedback()
      try {
        publish(await requestJson('/api/approve', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ session_id: current.value.session_id, approver_id: approver.value, approved, plan_hash: current.value.plan_hash }) }), approved ? '审批已执行' : '任务已拒绝')
        await loadRuns()
      } catch (caught) { error.value = caught.message } finally { busyAction.value = '' }
    }
    const recover = async () => {
      if (!current.value) return
      busyAction.value = 'recover'; clearFeedback()
      try {
        publish(await requestJson('/api/recover', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ session_id: current.value.session_id }) }), '已查询未知结果')
        await loadRuns()
      } catch (caught) { error.value = caught.message } finally { busyAction.value = '' }
    }
    const evidence = async () => {
      busyAction.value = 'evidence'; clearFeedback()
      try {
        evidenceResult.value = await requestJson('/api/evidence', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ path: evidencePath.value }) })
        notice.value = '已读取脱敏证据摘要'
      } catch (caught) { error.value = caught.message } finally { busyAction.value = '' }
    }
    const speak = async () => {
      busyAction.value = 'speak'; clearFeedback()
      try {
        const response = await fetch('/api/speak', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: speech.value }) })
        if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `请求失败 (${response.status})`)
        if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
        audioUrl.value = URL.createObjectURL(await response.blob()); notice.value = '语音已生成'
      } catch (caught) { error.value = caught.message } finally { busyAction.value = '' }
    }
    onMounted(async () => {
      window.addEventListener('hashchange', syncRoute)
      try { health.value = (await requestJson('/health')).status } catch { health.value = '不可用' }
      await loadRuns()
    })
    onUnmounted(() => window.removeEventListener('hashchange', syncRoute))
    return { approve, approver, audioUrl, busy, busyAction, canApprove, canRecover, candidates, checkpoints, completedRuns, current, error, evidence, evidencePath, evidenceResult, executionPlan, fileInput, formatDate, formatValue, health, labelState, loadRuns, navigate, navItems, notice, page, path, pendingRuns, receipt, recover, route, run, runs, selectRun, speak, speech, state, stateClass, trace }
  },
  template: `
    <div class="app-shell">
      <aside class="sidebar">
        <a class="brand" href="#/home" aria-label="DevBrief Agent 总览"><img src="/devbrief-mark.svg" alt="" class="brand-mark"><span class="brand-copy"><strong>DevBrief</strong><small>Agent workspace</small></span></a>
        <div class="workspace-switcher"><span class="workspace-dot"></span><span>研发工作区</span><span class="workspace-chevron">⌄</span></div>
        <nav class="primary-nav" aria-label="主导航"><a v-for="item in navItems" :key="item.id" :href="'#/' + item.id" :class="['nav-item', { active: route === item.id }]" :aria-current="route === item.id ? 'page' : undefined"><span class="nav-symbol">{{ item.id === 'home' ? '⌂' : item.id === 'triage' ? '◈' : item.id === 'evidence' ? '▤' : item.id === 'activity' ? '◷' : '◌' }}</span><span><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></span></a></nav>
        <div class="sidebar-footer"><div class="service-status"><span :class="['status-dot', health === 'ok' ? 'online' : 'offline']"></span><span>本地 API</span><strong>{{ health === 'ok' ? '在线' : health }}</strong></div><div class="sidebar-note">安全边界已启用<br><span>审批 · 幂等 · 可恢复</span></div></div>
      </aside>
      <div class="main-shell">
        <header class="topbar"><div class="topbar-context"><span class="overline">研发工作区</span><strong>{{ page.label }}</strong></div><div class="topbar-actions"><span class="environment-label">Local / Safe mode</span><button class="icon-button" title="刷新运行历史" aria-label="刷新运行历史" @click="loadRuns" :disabled="busyAction">↻</button><div class="avatar">{{ approver.slice(0, 1).toUpperCase() }}</div></div></header>
        <main class="page-content"><div v-if="error" class="feedback feedback-error" role="alert"><span>!</span>{{ error }}</div><div v-else-if="notice" class="feedback feedback-notice" role="status"><span>✓</span>{{ notice }}</div>
          <template v-if="route === 'home'"><section class="hero-section"><div class="hero-copy"><span class="kicker">EVIDENCE-DRIVEN AGENT</span><h1>把会议决定，<br><em>变成可交付任务。</em></h1><p>从转写到 GitHub Issue，每一步都有依据、审批和回执。让 Agent 做判断，让团队掌握最后一拍。</p><div class="hero-actions"><button class="button button-primary" @click="navigate('triage')">开始一次 Triage <span>→</span></button><button class="button button-link" @click="navigate('activity')">查看运行历史 <span>↗</span></button></div><div class="hero-proof"><span>●</span> 证据先行 <i></i><span>●</span> 人工审批 <i></i><span>●</span> 可恢复执行</div></div><div class="hero-visual"><img src="/devbrief-hero.png" alt="证据、计划与审批连接成一条可追溯工作流"><span>Decision trace / 01</span></div></section><section class="overview-band"><div class="section-intro"><span class="kicker">WORKSPACE PULSE</span><h2>今天的工作区</h2><p>从最近一次运行开始继续。</p></div><div class="metric-row"><div class="metric metric-primary"><span>总运行次数</span><strong>{{ runs.length }}</strong><small>所有本地会话</small></div><div class="metric"><span>已完成</span><strong>{{ completedRuns }}</strong><small>已生成外部回执</small></div><div class="metric"><span>待审批</span><strong>{{ pendingRuns }}</strong><small>需要你的判断</small></div></div></section><section class="home-lower"><div class="recent-section"><div class="section-heading"><div><span class="kicker">RECENT RUNS</span><h2>最近运行</h2></div><button class="text-button" @click="navigate('activity')">全部记录 <span>→</span></button></div><div v-if="runs.length" class="run-list"><button v-for="runItem in runs.slice(0, 4)" :key="runItem.session_id" class="run-row" @click="selectRun(runItem.session_id)"><span class="run-state"><span :class="['status-dot', runItem.state === 'completed' ? 'online' : 'pending']"></span>{{ labelState(runItem.state) }}</span><strong>{{ runItem.session_id }}</strong><span>{{ formatDate(runItem.created_at) }}</span><span class="row-arrow">↗</span></button></div><div v-else class="empty-inline"><span>还没有运行记录</span><button class="text-button" @click="navigate('triage')">创建第一条 <span>→</span></button></div></div><div class="workflow-section"><div class="section-heading"><div><span class="kicker">HOW IT WORKS</span><h2>一条清晰的路径</h2></div></div><ol class="workflow-list"><li><span>01</span><div><strong>导入会议输入</strong><p>上传脱敏转写或音频。</p></div></li><li><span>02</span><div><strong>生成证据计划</strong><p>Agent 绑定依据与风险。</p></div></li><li><span>03</span><div><strong>审批后执行</strong><p>只在明确批准后写入。</p></div></li></ol></div></section></template>
          <template v-else-if="route === 'triage'"><section class="page-heading"><div><span class="kicker">TRIAGE WORKSPACE</span><h1>把一次讨论，推进到可执行。</h1><p>输入、判断、计划和审批都在同一条路径上。</p></div><span v-if="current" :class="stateClass(state)">{{ labelState(state) }}</span></section><div class="triage-layout"><aside class="stage-rail"><div class="rail-label">RUN STAGES</div><div v-for="(stage, index) in ['输入','候选','计划','审批']" :key="stage" :class="['stage-item', { active: index === 0 ? !current : index === 1 ? current && !executionPlan : index === 2 ? executionPlan && canApprove : current && !canApprove }]" ><span>{{ String(index + 1).padStart(2, '0') }}</span><strong>{{ stage }}</strong><small>{{ ['等待 fixture','提取决策','绑定证据','确认写入'][index] }}</small></div><div class="rail-tip"><span class="status-dot online"></span><p>外部写入默认需要人工批准。</p></div></aside><div class="triage-main"><section class="focus-section"><div class="focus-heading"><div><span class="kicker">01 / INPUT</span><h2>从一份可信输入开始</h2></div><span class="section-meta">JSON fixture 或音频</span></div><form class="triage-form" @submit.prevent="run"><label class="drop-field"><span class="drop-icon">＋</span><span><strong>上传 fixture 或音频</strong><small>WAV、MP3、M4A、OGG、WebM，或 JSON</small></span><input ref="fileInput" type="file" accept=".json,.wav,.mp3,.m4a,.ogg,.webm"></label><label class="path-field"><span>工作区路径</span><input v-model="path" aria-label="fixture path"></label><button class="button button-primary" type="submit" :disabled="busy">{{ busy ? '正在分析' : '生成分析计划' }} <span>→</span></button></form></section><section v-if="current" class="focus-section"><div class="focus-heading"><div><span class="kicker">02 / SIGNALS</span><h2>候选与证据</h2></div><span class="section-meta">{{ candidates.length }} 条候选</span></div><div class="candidate-stream"><article v-for="candidate in candidates" :key="candidate.candidate_id" class="candidate-item"><div class="candidate-marker"></div><div><div class="candidate-title"><strong>{{ candidate.statement }}</strong><span>{{ candidate.kind }}</span></div><p>优先级 <b>{{ formatValue(candidate.priority) }}</b><i></i> 负责人 <b>{{ formatValue(candidate.owner) }}</b><i></i> 截止 <b>{{ formatValue(candidate.due_at) }}</b></p><div class="evidence-ref" v-for="item in candidate.evidence" :key="item.reference"><span>↳</span><code>{{ item.reference }}</code></div></div></article></div></section><section v-if="executionPlan" class="focus-section"><div class="focus-heading"><div><span class="kicker">03 / PLAN</span><h2>执行计划</h2></div><code class="hash-chip">{{ current.plan_hash?.slice(0, 20) }}…</code></div><div class="plan-preview"><h3>{{ executionPlan.title }}</h3><p>{{ executionPlan.body }}</p><dl><div><dt>目标仓库</dt><dd>{{ executionPlan.repository }}</dd></div><div><dt>标签</dt><dd>{{ formatValue(executionPlan.labels) }}</dd></div><div><dt>指派</dt><dd>{{ formatValue(executionPlan.assignee) }}</dd></div></dl></div></section><section v-if="current" class="approval-bar"><div><span class="kicker">04 / APPROVAL · 审批与回执</span><strong>{{ canApprove ? '确认后才会创建 GitHub Issue' : '这次运行已经留下回执' }}</strong><small>{{ canApprove ? '检查计划内容、目标仓库和风险，再选择下一步。' : '所有外部对象与幂等信息都已记录。' }}</small></div><label v-if="canApprove" class="approver-inline"><span>审批人</span><input v-model="approver" aria-label="approver"></label><div class="approval-actions"><button v-if="canApprove" class="button button-primary" @click="approve(true)" :disabled="busyAction"><span>✓</span>批准并执行</button><button v-if="canApprove" class="button button-danger" @click="approve(false)" :disabled="busyAction">拒绝</button><button v-if="canRecover" class="button button-secondary" @click="recover" :disabled="busyAction">查询未知结果</button></div></section><section v-if="receipt" class="receipt-strip"><div><span class="kicker">RECEIPT</span><strong>{{ labelState(receipt.status) }}</strong></div><div><span>Provider</span><code>{{ formatValue(receipt.provider_request_id) }}</code></div><div><span>外部对象</span><a v-if="receipt.external_url" :href="receipt.external_url" target="_blank" rel="noreferrer">Issue #{{ receipt.external_object_id }} ↗</a><code v-else>{{ formatValue(receipt.external_object_id) }}</code></div><div><span>Trace / Checkpoint</span><strong>{{ trace?.spans ?? 0 }} / {{ checkpoints?.count ?? 0 }}</strong></div></section><div v-if="!current" class="blank-state"><div class="blank-orbit"><img src="/devbrief-mark.svg" alt=""></div><h2>准备好开始了吗？</h2><p>上传一份脱敏输入，Agent 会先生成可审阅计划，不会直接写入外部系统。</p></div></div></div></template>
          <template v-else-if="route === 'evidence'"><section class="page-heading"><div><span class="kicker">EVIDENCE BROWSER</span><h1>只看需要相信的依据。</h1><p>从工作区读取有界文本，返回引用、摘要与哈希。</p></div></section><section class="evidence-layout"><form class="evidence-search" @submit.prevent="evidence"><label><span>工作区路径</span><input v-model="evidencePath" aria-label="repository path" placeholder="例如 README.md"></label><button class="button button-primary" type="submit" :disabled="busyAction === 'evidence'">{{ busyAction === 'evidence' ? '读取中' : '读取证据' }} <span>→</span></button></form><div v-if="evidenceResult" class="evidence-result"><div class="result-heading"><div><span class="kicker">BOUNDED RESULT</span><h2>{{ evidenceResult.path }}</h2></div><span>{{ evidenceResult.bytes_read }} bytes</span></div><div class="result-grid"><div><span>引用</span><code>{{ evidenceResult.reference }}</code></div><div><span>摘要哈希</span><code>{{ evidenceResult.digest }}</code></div></div><p>{{ evidenceResult.summary }}</p></div><div v-else class="blank-state"><div class="blank-orbit"><img src="/devbrief-mark.svg" alt=""></div><h2>还没有打开证据</h2><p>输入仓库内路径，查看可追溯的摘要结果。</p></div></section></template>
          <template v-else-if="route === 'activity'"><section class="page-heading"><div><span class="kicker">RUN HISTORY</span><h1>每一次判断，都有记录。</h1><p>选择一个会话，回到它的计划、审批和回执。</p></div><button class="button button-primary" @click="navigate('triage')">新建 Triage <span>→</span></button></section><section class="activity-section"><div class="table-toolbar"><div><span class="kicker">{{ runs.length }} SESSIONS</span><h2>运行记录</h2></div><button class="text-button" @click="loadRuns">刷新 <span>↻</span></button></div><div v-if="runs.length" class="activity-table-wrap"><table class="activity-table"><thead><tr><th>会话</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="runItem in runs" :key="runItem.session_id"><td><button class="table-link" @click="selectRun(runItem.session_id)">{{ runItem.session_id }}</button></td><td><span class="run-state"><span :class="['status-dot', runItem.state === 'completed' ? 'online' : 'pending']"></span>{{ labelState(runItem.state) }}</span></td><td>{{ formatDate(runItem.created_at) }}</td><td><button class="row-action" @click="selectRun(runItem.session_id)">打开 ↗</button></td></tr></tbody></table></div><div v-else class="blank-state"><div class="blank-orbit"><img src="/devbrief-mark.svg" alt=""></div><h2>运行历史为空</h2><p>完成第一次 Triage 后，这里会成为你的审计时间线。</p><button class="button button-secondary" @click="navigate('triage')">开始一次 Triage <span>→</span></button></div></section></template>
          <template v-else><section class="page-heading"><div><span class="kicker">VOICE LAB</span><h1>让 Agent 的结果，也能被听见。</h1><p>使用百炼 TTS 生成播报音频，ASR 上传入口在 Triage 工作台。</p></div></section><section class="audio-layout"><div class="audio-copy"><span class="kicker">TEXT TO SPEECH</span><h2>生成一次简短播报</h2><p>播报内容只在请求时发送，页面不会保存凭据。</p></div><form class="audio-form" @submit.prevent="speak"><label><span>播报文本</span><textarea v-model="speech" aria-label="speech text" rows="4"></textarea></label><button class="button button-primary" type="submit" :disabled="busyAction === 'speak'">{{ busyAction === 'speak' ? '生成中' : '生成音频' }} <span>→</span></button></form><div v-if="audioUrl" class="audio-result"><span class="status-dot online"></span><strong>音频已准备好</strong><audio :src="audioUrl" controls></audio></div></section></template>
        </main>
      </div>
    </div>
  `,
}).mount('#app')
