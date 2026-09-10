import { createApp, ref } from 'vue'
import './style.css'

const requestJson = async (url, options = {}) => {
  const response = await fetch(url, options)
  return response.json()
}

createApp({
  setup() {
    const health = ref('检查中')
    const busy = ref(false)
    const path = ref('fixtures/transcripts/bug-triage-redacted-v1.json')
    const evidencePath = ref('README.md')
    const sessionId = ref('')
    const approver = ref('human-web')
    const speech = ref('准备提交任务')
    const runs = ref([])
    const output = ref({})
    const audioUrl = ref('')

    const setOutput = (value) => {
      output.value = value
      if (value.session_id) sessionId.value = value.session_id
    }
    const loadRuns = async () => {
      const value = await requestJson('/api/runs')
      runs.value = value.runs || []
    }
    const run = async (event) => {
      busy.value = true
      try {
        const file = event.target.elements.file.files[0]
        const response = file
          ? await requestJson('/api/run', { method: 'POST', body: new FormData(event.target) })
          : await requestJson('/api/run', {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ path: path.value }),
            })
        setOutput(response)
        await loadRuns()
      } finally {
        busy.value = false
      }
    }
    const approve = async (approved) => {
      setOutput(await requestJson('/api/approve', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId.value,
          approver_id: approver.value,
          approved,
          plan_hash: output.value.plan_hash,
        }),
      }))
      await loadRuns()
    }
    const recover = async () => {
      setOutput(await requestJson('/api/recover', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId.value }),
      }))
      await loadRuns()
    }
    const evidence = async () => {
      setOutput(await requestJson('/api/evidence', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path: evidencePath.value }),
      }))
    }
    const speak = async () => {
      const response = await fetch('/api/speak', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text: speech.value }),
      })
      if (!response.ok) return setOutput(await response.json())
      if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
      audioUrl.value = URL.createObjectURL(await response.blob())
    }

    requestJson('/health').then((value) => { health.value = value.status }).catch(() => { health.value = '不可用' })
    return { approve, approver, audioUrl, busy, evidence, evidencePath, health, loadRuns, output, path, recover, run, runs, sessionId, speak, speech }
  },
  template: `
    <header><div><strong>DevBrief Agent</strong><span>Evidence-driven task control</span></div><em>{{ health }}</em></header>
    <main>
      <section><h1>研发决策控制台</h1><p>从脱敏 fixture 或音频生成可审阅任务，明确批准后才允许创建 Issue。</p></section>
      <section><h2>输入</h2><form @submit.prevent="run"><input name="file" type="file" accept=".json,audio/*"><input v-model="path" aria-label="fixture path"><button :disabled="busy">{{ busy ? '处理中' : '运行 Triage' }}</button></form></section>
      <section><h2>审批</h2><input v-model="sessionId" placeholder="session id"><input v-model="approver" placeholder="approver"><button @click="approve(true)" :disabled="!sessionId">批准并执行</button><button class="danger" @click="approve(false)" :disabled="!sessionId">拒绝</button><button class="secondary" @click="recover" :disabled="!sessionId">查询未知结果</button></section>
      <section><h2>证据</h2><form @submit.prevent="evidence"><input v-model="evidencePath" aria-label="repository path"><button>读取工作区证据</button></form></section>
      <section><h2>语音</h2><form @submit.prevent="speak"><input v-model="speech" aria-label="speech text"><button>生成</button></form><audio v-if="audioUrl" :src="audioUrl" controls></audio></section>
      <section><h2>运行记录</h2><button class="secondary" @click="loadRuns">刷新</button><pre>{{ JSON.stringify(runs, null, 2) }}</pre></section>
      <section class="wide"><h2>结果与审计</h2><pre>{{ JSON.stringify(output, null, 2) }}</pre></section>
    </main>
  `,
}).mount('#app')
