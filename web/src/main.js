import { computed, createApp, onMounted, ref } from 'vue'
import './style.css'

const stateLabels = {
  awaiting_approval: '等待审批',
  completed: '已完成',
  executing: '执行中',
  rejected: '已拒绝',
  expired: '已过期',
  failed_recoverable: '可恢复失败',
  failed_terminal: '终止失败',
  cancelled: '已取消',
  pending: '待审批',
  approved: '已批准',
  consumed: '已消费',
  succeeded: '已写入',
  unknown_outcome: '结果未知',
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`)
  return payload
}

createApp({
  setup() {
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

    const candidates = computed(() => current.value?.candidates || [])
    const executionPlan = computed(() => current.value?.execution_plan || null)
    const receipt = computed(() => current.value?.receipt || null)
    const trace = computed(() => current.value?.trace_summary || null)
    const checkpoints = computed(() => current.value?.checkpoint_summary || null)
    const state = computed(() => current.value?.state || '')
    const canApprove = computed(() => state.value === 'awaiting_approval')
    const canRecover = computed(() => receipt.value?.status === 'unknown_outcome')

    const clearFeedback = () => {
      error.value = ''
      notice.value = ''
    }
    const publish = (result, message = '') => {
      current.value = result
      notice.value = message
      error.value = ''
    }
    const labelState = (value) => stateLabels[value] || value || '未创建'
    const stateClass = (value) => `state state-${value || 'empty'}`
    const formatValue = (value) => {
      if (value === null || value === undefined || value === '') return '未设置'
      return Array.isArray(value) ? value.join('、') || '未设置' : String(value)
    }

    const loadRuns = async () => {
      try {
        const value = await requestJson('/api/runs')
        runs.value = value.runs || []
      } catch (caught) {
        error.value = caught.message
      }
    }
    const selectRun = async (sessionId) => {
      busyAction.value = `history-${sessionId}`
      clearFeedback()
      try {
        publish(await requestJson(`/api/runs/${encodeURIComponent(sessionId)}`), '已加载运行记录')
      } catch (caught) {
        error.value = caught.message
      } finally {
        busyAction.value = ''
      }
    }
    const run = async () => {
      busy.value = true
      clearFeedback()
      try {
        const file = fileInput.value?.files?.[0]
        const result = file
          ? await requestJson('/api/run', {
              method: 'POST',
              body: (() => {
                const form = new FormData()
                form.append('file', file)
                return form
              })(),
            })
          : await requestJson('/api/run', {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ path: path.value }),
            })
        publish(result, '已生成待审批任务')
        await loadRuns()
      } catch (caught) {
        error.value = caught.message
      } finally {
        busy.value = false
      }
    }
    const approve = async (approved) => {
      if (!current.value) return
      busyAction.value = approved ? 'approve' : 'reject'
      clearFeedback()
      try {
        publish(
          await requestJson('/api/approve', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              session_id: current.value.session_id,
              approver_id: approver.value,
              approved,
              plan_hash: current.value.plan_hash,
            }),
          }),
          approved ? '审批已执行' : '任务已拒绝',
        )
        await loadRuns()
      } catch (caught) {
        error.value = caught.message
      } finally {
        busyAction.value = ''
      }
    }
    const recover = async () => {
      if (!current.value) return
      busyAction.value = 'recover'
      clearFeedback()
      try {
        publish(
          await requestJson('/api/recover', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ session_id: current.value.session_id }),
          }),
          '已查询未知结果',
        )
        await loadRuns()
      } catch (caught) {
        error.value = caught.message
      } finally {
        busyAction.value = ''
      }
    }
    const evidence = async () => {
      busyAction.value = 'evidence'
      clearFeedback()
      try {
        evidenceResult.value = await requestJson('/api/evidence', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ path: evidencePath.value }),
        })
        notice.value = '已读取脱敏证据摘要'
      } catch (caught) {
        error.value = caught.message
      } finally {
        busyAction.value = ''
      }
    }
    const speak = async () => {
      busyAction.value = 'speak'
      clearFeedback()
      try {
        const response = await fetch('/api/speak', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ text: speech.value }),
        })
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}))
          throw new Error(payload.error || `请求失败 (${response.status})`)
        }
        if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
        audioUrl.value = URL.createObjectURL(await response.blob())
        notice.value = '语音已生成'
      } catch (caught) {
        error.value = caught.message
      } finally {
        busyAction.value = ''
      }
    }

    onMounted(async () => {
      try {
        health.value = (await requestJson('/health')).status
      } catch {
        health.value = '不可用'
      }
      await loadRuns()
    })

    return {
      approver,
      approve,
      audioUrl,
      busy,
      busyAction,
      canApprove,
      canRecover,
      candidates,
      checkpoints,
      current,
      error,
      evidence,
      evidencePath,
      evidenceResult,
      executionPlan,
      fileInput,
      formatValue,
      health,
      labelState,
      loadRuns,
      notice,
      path,
      receipt,
      recover,
      run,
      runs,
      selectRun,
      speak,
      speech,
      state,
      stateClass,
      trace,
    }
  },
  template: `
    <header class="topbar">
      <div class="brand"><strong>DevBrief Agent</strong><span>研发决策控制台</span></div>
      <span class="health" :class="health === 'ok' ? 'health-ok' : 'health-error'">{{ health }}</span>
    </header>

    <main class="console">
      <section class="workspace-heading">
        <div><h1>Bug Triage</h1><p>证据、计划、审批与回执</p></div>
        <div class="session-identifiers" v-if="current"><span>会话</span><code>{{ current.session_id }}</code><span>Trace</span><code>{{ current.trace_id }}</code></div>
      </section>

      <div v-if="error" class="feedback feedback-error" role="alert">{{ error }}</div>
      <div v-else-if="notice" class="feedback feedback-notice" role="status">{{ notice }}</div>

      <section class="panel run-panel">
        <div class="panel-heading"><h2>输入</h2><span v-if="busy" class="loading">处理中</span></div>
        <form class="input-form" @submit.prevent="run">
          <label class="file-input"><span>上传 fixture 或音频</span><input ref="fileInput" type="file" accept=".json,.wav,.mp3,.m4a,.ogg,.webm"></label>
          <label class="path-input"><span>工作区 fixture</span><input v-model="path" aria-label="fixture path"></label>
          <button type="submit" :disabled="busy">运行 Triage</button>
        </form>
      </section>

      <section class="panel status-panel">
        <div class="panel-heading"><h2>会话状态</h2><span :class="stateClass(state)">{{ labelState(state) }}</span></div>
        <div class="status-grid">
          <div><span>候选</span><strong>{{ candidates.length }}</strong></div>
          <div><span>Trace spans</span><strong>{{ trace?.spans ?? 0 }}</strong></div>
          <div><span>Checkpoints</span><strong>{{ checkpoints?.count ?? 0 }}</strong></div>
        </div>
        <dl class="detail-list" v-if="current">
          <div><dt>Plan hash</dt><dd><code>{{ current.plan_hash }}</code></dd></div>
          <div><dt>审批</dt><dd>{{ labelState(current.approval_status) }}</dd></div>
        </dl>
      </section>

      <section class="panel candidates-panel">
        <div class="panel-heading"><h2>候选与证据</h2><span>{{ candidates.length ? candidates.length + ' 条' : '暂无候选' }}</span></div>
        <ol v-if="candidates.length" class="candidate-list">
          <li v-for="candidate in candidates" :key="candidate.candidate_id">
            <div class="candidate-title"><strong>{{ candidate.statement }}</strong><span>{{ candidate.kind }}</span></div>
            <dl class="candidate-fields"><div><dt>优先级</dt><dd>{{ formatValue(candidate.priority) }}</dd></div><div><dt>负责人</dt><dd>{{ formatValue(candidate.owner) }}</dd></div><div><dt>期限</dt><dd>{{ formatValue(candidate.due_at) }}</dd></div><div><dt>状态</dt><dd>{{ formatValue(candidate.status) }}</dd></div></dl>
            <ul class="references"><li v-for="evidence in candidate.evidence" :key="evidence.reference"><code>{{ evidence.reference }}</code></li></ul>
          </li>
        </ol>
      </section>

      <section class="panel plan-panel">
        <div class="panel-heading"><h2>执行计划</h2><span v-if="executionPlan">{{ executionPlan.tool_name }}</span></div>
        <template v-if="executionPlan">
          <h3>{{ executionPlan.title }}</h3>
          <dl class="detail-list"><div><dt>目标仓库</dt><dd>{{ executionPlan.repository }}</dd></div><div><dt>标签</dt><dd>{{ formatValue(executionPlan.labels) }}</dd></div><div><dt>指派</dt><dd>{{ formatValue(executionPlan.assignee) }}</dd></div></dl>
          <p class="plan-body">{{ executionPlan.body }}</p>
        </template>
        <p v-else class="empty-state">运行后显示签发计划</p>
      </section>

      <section class="panel approval-panel">
        <div class="panel-heading"><h2>审批与回执</h2><span :class="stateClass(receipt?.status)">{{ receipt ? labelState(receipt.status) : labelState(current?.approval_status) }}</span></div>
        <label class="approver-field"><span>审批人</span><input v-model="approver" aria-label="approver"></label>
        <div class="actions"><button @click="approve(true)" :disabled="!canApprove || busyAction" :aria-busy="busyAction === 'approve'">批准并执行</button><button class="danger" @click="approve(false)" :disabled="!canApprove || busyAction">拒绝</button><button class="secondary" @click="recover" :disabled="!canRecover || busyAction">查询未知结果</button></div>
        <dl v-if="current?.approval_id" class="detail-list"><div><dt>批准 ID</dt><dd><code>{{ current.approval_id }}</code></dd></div><div><dt>批准状态</dt><dd>{{ labelState(current.approval_status) }}</dd></div></dl>
        <dl v-if="receipt" class="detail-list receipt-list"><div><dt>幂等键</dt><dd><code>{{ receipt.idempotency_key }}</code></dd></div><div><dt>Provider 请求</dt><dd>{{ formatValue(receipt.provider_request_id) }}</dd></div><div><dt>外部对象</dt><dd><a v-if="receipt.external_url" :href="receipt.external_url" target="_blank" rel="noreferrer">{{ receipt.external_object_id }}</a><span v-else>{{ formatValue(receipt.external_object_id) }}</span></dd></div></dl>
      </section>

      <section class="panel evidence-panel">
        <div class="panel-heading"><h2>仓库证据</h2><span v-if="evidenceResult">已读取</span></div>
        <form class="compact-form" @submit.prevent="evidence"><input v-model="evidencePath" aria-label="repository path"><button :disabled="busyAction">读取</button></form>
        <dl v-if="evidenceResult" class="detail-list"><div><dt>引用</dt><dd><code>{{ evidenceResult.reference }}</code></dd></div><div><dt>摘要</dt><dd>{{ evidenceResult.summary }}</dd></div><div><dt>摘要哈希</dt><dd><code>{{ evidenceResult.digest }}</code></dd></div></dl>
      </section>

      <section class="panel speech-panel">
        <div class="panel-heading"><h2>语音播报</h2><span v-if="audioUrl">可播放</span></div>
        <form class="compact-form" @submit.prevent="speak"><input v-model="speech" aria-label="speech text"><button :disabled="busyAction">生成</button></form>
        <audio v-if="audioUrl" :src="audioUrl" controls></audio>
      </section>

      <section class="panel history-panel">
        <div class="panel-heading"><h2>运行历史</h2><button class="icon-button" title="刷新运行历史" aria-label="刷新运行历史" @click="loadRuns" :disabled="busyAction">↻</button></div>
        <div class="history-scroll"><table><thead><tr><th>会话</th><th>状态</th><th>创建时间</th></tr></thead><tbody><tr v-for="runItem in runs" :key="runItem.session_id" :class="{ selected: runItem.session_id === current?.session_id }"><td><button class="history-link" @click="selectRun(runItem.session_id)" :disabled="busyAction === 'history-' + runItem.session_id">{{ runItem.session_id }}</button></td><td><span :class="stateClass(runItem.state)">{{ labelState(runItem.state) }}</span></td><td>{{ runItem.created_at }}</td></tr><tr v-if="!runs.length"><td colspan="3" class="empty-state">暂无运行记录</td></tr></tbody></table></div>
      </section>
    </main>
  `,
}).mount('#app')
