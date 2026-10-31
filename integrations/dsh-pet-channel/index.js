/**
 * dsh-pet-channel —— 让桌宠（petcore）当 DSH 的另一个客户端。
 *
 * 形状照抄 dsh-qq-group（同一台机器上跑了很久的那条）：
 *   桌宠 → POST /pet/prompt     一句话交给本机 agent
 *   桌宠 ← GET  /pet/events     SSE：user / thinking 轮次 / 工具调用 / 正文 / 审批 / idle
 *   桌宠 → POST /pet/answer     审批卡点「同意 / 拒绝」
 *   桌宠 → POST /pet/cancel     掐掉当前这一轮
 *
 * 三条硬约束（都是别人踩过的坑，配方见 docs/qq-recipe.md）：
 *   1. 只绑 127.0.0.1，并且每个请求都要带 x-pet-token（qq-group 没有鉴权，我们补上）。
 *   2. 事件流不是订阅：每 300ms 轮询 sessionQuery.observeSession，按 seq 水位差量推送。
 *   3. 审批不走「agent 卡住等答复」：工具在 tools/pre-execute 被拒绝（返回 reason），
 *      桌宠点同意 → 记一次性通行证 → 再开一轮 followup 让它接着做。
 */
import { createServer } from 'node:http'
import { randomUUID } from 'node:crypto'
import {
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
  appendFileSync,
  statSync,
  rmSync,
  renameSync,
} from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

export const name = 'pet-channel'
export const inject = ['agents', 'sessionQuery', 'tools']

//: 命令记录与事件记录同一个目录；文件名也以 dsh* 开头，桌宠的 GlobTailer 能扫到。
const BOOT_ID = randomUUID().replace(/-/g, '')
const COMMAND_MAX_BYTES = 1024 * 1024

const VERSION = '0.1.0'

const DEFAULTS = {
  port: 6791,
  host: '127.0.0.1',
  token: '',
  cwd: '',
  provider: undefined,
  model: undefined,
  reasoningEffort: undefined,
  maxTokens: undefined,
  sandboxMode: 'danger-full-access',
  agentPreset: 'cordis',
  askApproval: false,
  pollMs: 300,
  maxTurns: 0,
  maxBodyBytes: 2 * 1024 * 1024,
  grantMinutes: 5,
  //: 审批等待上限：超时自动取消，避免 DSH 的 whenIdle 永久挂起（桌宠侧看起来像卡死）。
  approvalTimeoutMinutes: 10,
}

const SHELL_TOOLS = new Set(['bash', 'ops_bash', 'pwsh', 'shell'])
const FILE_WRITE_TOOLS = new Set(['write', 'edit', 'multi_edit', 'apply_patch', 'ops_replace'])
/**
 * 危险命令：只在「命令行的开头，或者 ; & | 之后」才算数。
 * 之前写成 /format\b/ 会把 `Format-Table` 这种只读命令也判成危险（2026-10-27 实测误拦）。
 */
const DESTRUCTIVE_CMD = /(^|[;&|]\s*|\bthen\s+|\bdo\s+)(rm|del|rd|rmdir|remove-item|erase|format-volume|diskpart|shutdown|taskkill|stop-process|kill|reg\s+delete|net\s+user|takeown|icacls|cipher\s+\/w)\b|\bformat\s+[a-z]:/i

const textOf = (content) => {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .filter((b) => b && b.type === 'text' && typeof b.text === 'string')
    .map((b) => b.text)
    .join('')
}

const clip = (s, n) => {
  const t = String(s ?? '')
  return t.length > n ? `${t.slice(0, n)}…` : t
}

/** 工具参数真实形状是 `{ name, arguments: "{json 字符串}" }`（qq-group 踩过）。 */
function toolArgs(exec) {
  const raw = exec?.input ?? exec?.args ?? exec?.params ?? exec?.arguments
  if (raw == null) return {}
  if (typeof raw === 'string') {
    try { return JSON.parse(raw) } catch { return {} }
  }
  return typeof raw === 'object' ? raw : {}
}

/** 这次工具调用要不要先问桌宠。拿不到参数就按危险处理（fail closed）。 */
function needsApproval(exec) {
  const tool = String(exec?.name ?? '')
  const args = toolArgs(exec)
  if (SHELL_TOOLS.has(tool)) {
    const cmd = String(args.command ?? args.cmd ?? '')
    return cmd ? DESTRUCTIVE_CMD.test(cmd) : true
  }
  if (FILE_WRITE_TOOLS.has(tool)) {
    const p = String(args.path ?? args.file_path ?? args.filePath ?? '')
    return !p
  }
  return false
}

export function apply(ctx, rawConfig = {}) {
  try {
    start(ctx, rawConfig)
  } catch (err) {
    try { console.error('[pet-channel] apply failed', err) } catch { /* noop */ }
  }
}

function start(ctx, rawConfig) {
  const cfg = { ...DEFAULTS, ...rawConfig }
  cfg.port = Number(cfg.port) || DEFAULTS.port
  cfg.pollMs = Math.max(120, Number(cfg.pollMs) || DEFAULTS.pollMs)

  const home = homedir()
  const stateDir = join(home, '.dsh', 'pet-channel')
  const stateFile = join(stateDir, 'state.json')
  const infoFile = cfg.infoFile
    || join(process.env.APPDATA || join(home, 'AppData', 'Roaming'), 'dsh-pet-bridge', 'pet-channel.json')

  const log = (...a) => {
    try {
      if (ctx.logger?.info) ctx.logger.info('[pet-channel]', ...a)
      else console.log('[pet-channel]', ...a)
    } catch { /* noop */ }
  }

  // ---------- 持久状态 ----------
  let state = { sessions: {}, token: '', approved: 0, rejected: 0 }
  try {
    if (existsSync(stateFile)) state = { ...state, ...JSON.parse(readFileSync(stateFile, 'utf8')) }
  } catch { log('state file unreadable, starting fresh') }
  if (!state.token) state.token = randomUUID().replace(/-/g, '')
  if (cfg.token) state.token = String(cfg.token)
  const token = state.token

  const saveState = () => {
    try {
      mkdirSync(stateDir, { recursive: true })
      writeFileSync(stateFile, JSON.stringify(state, null, 2), 'utf8')
    } catch (e) { log('save state failed', String(e?.message ?? e)) }
  }
  saveState()

  // ---------- 桌宠控制（命令记录） ----------
  const commandDir = join(
    process.env.APPDATA || join(home, 'AppData', 'Roaming'),
    'dsh-pet-bridge',
  )
  const commandFile = join(commandDir, `dsh-petcmd-${process.pid}.jsonl`)
  let lastCommandAt = 0

  /** 写一条 pet/command；返回 false 表示参数不合法或被限流。 */
  function writePetCommand(cmd, fields = {}) {
    const action = String(cmd ?? '').trim().toLowerCase()
    if (!['say', 'play_animation', 'notify'].includes(action)) return false
    const now = Date.now()
    if (now - lastCommandAt < 1000) return false
    lastCommandAt = now
    const text = clip(fields.text ?? '', 200)
    const name = clip(fields.name ?? fields.animation ?? '', 64)
    const duration = Math.max(0, Math.min(10000, Number(fields.duration_ms) || 0))
    if (action === 'play_animation' && !name) return false
    if (action !== 'play_animation' && !text) return false
    const record = {
      ts: now / 1000,
      agent: 'dsh',
      event: 'pet/command',
      cmd: action,
      inst: BOOT_ID,
    }
    if (text) record.text = text
    if (name) record.name = name
    if (duration) record.duration_ms = duration
    try {
      mkdirSync(commandDir, { recursive: true })
      try {
        if (existsSync(commandFile) && statSync(commandFile).size > COMMAND_MAX_BYTES) {
          rmSync(`${commandFile}.1`, { force: true })
          renameSync(commandFile, `${commandFile}.1`)
        }
      } catch { /* 轮转失败不阻塞写入 */ }
      appendFileSync(commandFile, `${JSON.stringify(record)}\n`, 'utf8')
      return true
    } catch (e) {
      log('write pet command failed', String(e?.message ?? e))
      return false
    }
  }

  registerPetControl(ctx, writePetCommand, log).catch((e) => {
    log('pet_control setup failed', String(e?.message ?? e))
  })

  //: 斜杠命令兜底：模型不需要工具也能驱动桌宠。
  try {
    ctx.on('session/event', (_session, event) => {
      if (event?.type !== 'user/message') return
      const text = textOf(event?.data?.content ?? event?.data?.message?.content).trim()
      const match = /^\/pet\s+(say|play|notify)\s+([\s\S]+)$/i.exec(text)
      if (!match) return
      const kind = match[1].toLowerCase()
      const value = match[2].trim()
      if (kind === 'play') writePetCommand('play_animation', { name: value })
      else writePetCommand(kind, { text: value })
    })
  } catch (e) {
    log('slash command hook failed', String(e?.message ?? e))
  }

  /** key -> { sessionId, agent, watermark, poller, chain } */
  const lanes = new Map()
  /** SSE 客户端 */
  const clients = new Set()
  /** 等桌宠点头的审批：id -> { sessionId, tool, args, at } */
  const pending = new Map()
  /** 一次性通行证：sessionId -> { tool, until } */
  const grants = new Map()
  /** 已经挂过闸的会话 */
  const gated = new Set()

  // ---------- 推送 ----------
  function broadcast(frame) {
    const payload = `data: ${JSON.stringify(frame)}\n\n`
    for (const res of clients) {
      try { res.write(payload) } catch { clients.delete(res) }
    }
  }
  const push = (kind, data = {}) => broadcast({ kind, time: Date.now(), ...data })

  // ---------- 读事件 ----------
  async function readEvents(sessionId) {
    const q = ctx.get('sessionQuery')
    if (typeof q?.observeSession !== 'function') return []
    let obs
    try { obs = await q.observeSession(sessionId, { projectionMode: 'none' }) } catch { return [] }
    try { return Array.isArray(obs?.events) ? obs.events : [] } finally {
      try { obs?.[Symbol.dispose]?.() } catch { /* noop */ }
    }
  }

  // ---------- 审批闸 ----------
  function gate(exec, next, sessionId) {
    if (!cfg.askApproval) return next()
    if (!needsApproval(exec)) return next()

    const tool = String(exec?.name ?? '')
    const grant = grants.get(sessionId)
    if (grant && grant.until > Date.now() && grant.tool === tool) {
      grants.delete(sessionId) // 用掉即焚
      log('grant consumed', tool)
      return next()
    }

    // 同一会话只留一个待审批：旧卡还没点就来了新审批时，取消旧卡再挂新卡。
    // 否则桌宠侧会堆出一串永远等不到结果的卡片，DSH 车道也会一直占着。
    for (const [oldId, old] of pending) {
      if (old.sessionId !== sessionId) continue
      pending.delete(oldId)
      clearTimeout(old.timer)
      push('approval-resolved', { id: oldId, allow: false, cancelled: true, tool: old.tool })
    }

    const id = randomUUID()
    const args = clip(JSON.stringify(toolArgs(exec)), 1200)
    const timeoutMs = Math.max(1, Number(cfg.approvalTimeoutMinutes) || 10) * 60_000
    const timer = setTimeout(() => {
      const item = pending.get(id)
      if (!item) return
      pending.delete(id)
      push('approval-resolved', { id, allow: false, cancelled: true, tool: item.tool })
      log('approval timed out', item.tool, id)
      submit('（审批等待超时，这次操作先不做了。如果还需要做，请主人重新发起。）', {
        internal: true,
      }).catch(() => { /* 超时后的收尾失败不致命 */ })
    }, timeoutMs)
    timer.unref?.()
    pending.set(id, { sessionId, tool, args, at: Date.now(), timer })
    push('approval-request', {
      id,
      sessionId,
      tool,
      args,
      grantMinutes: cfg.grantMinutes,
      timeoutMinutes: Math.round(timeoutMs / 60_000),
      title: `要跑一下 ${tool}`,
    })
    log('gated', tool, id)
    return {
      reason: `「${tool}」这类操作我拦下来了，已经推到桌宠上等点同意。`
        + '主人点同意后我会接着做，你不用再问一遍。',
    }
  }

  function attachGate(agent, sessionId) {
    if (!cfg.askApproval || gated.has(sessionId)) return
    const on = agent?.ctx?.on
    if (typeof on !== 'function') return
    try {
      on.call(agent.ctx, 'tools/pre-execute', (exec, next) => gate(exec, next, sessionId))
      gated.add(sessionId)
      log('gate attached', sessionId)
    } catch (e) { log('attach gate failed', String(e?.message ?? e)) }
  }

  function answer(id, allow) {
    const p = pending.get(id)
    if (!p) return false
    pending.delete(id)
    clearTimeout(p.timer)
    if (allow) {
      grants.set(p.sessionId, { tool: p.tool, until: Date.now() + cfg.grantMinutes * 60_000 })
      state.approved = (state.approved ?? 0) + 1
      saveState()
      push('approval-resolved', { id, allow: true, tool: p.tool })
      // 拒绝是「工具被挡下、模型收到 reason」；同意要再推一轮让它接着做
      submit(
        `（主人在桌宠上点了同意，「${p.tool}」已经放行，继续刚才被拦下的那个操作，不用再问一遍。）`,
        { internal: true },
      ).catch((e) => log('resume after approval failed', String(e?.message ?? e)))
    } else {
      state.rejected = (state.rejected ?? 0) + 1
      saveState()
      push('approval-resolved', { id, allow: false, tool: p.tool })
      submit('（主人在桌宠上拒绝了这次操作，别绕开它，说一句你打算怎么换个做法。）', { internal: true })
        .catch(() => { /* 拒绝后的收尾失败不致命 */ })
    }
    return true
  }

  // ---------- 会话车道 ----------
  async function resolveCwd() {
    if (cfg.cwd) return cfg.cwd
    try {
      const fs = ctx.get('fs')
      if (fs && typeof fs.resolve === 'function') {
        const p = await fs.resolve('.')
        if (typeof fs.processPath === 'function') return fs.processPath(p)
        if (typeof p === 'string') return p
      }
    } catch { /* 落到 process.cwd() */ }
    return process.cwd()
  }

  /**
   * 建会话走 sessionController（GUI 自己的入口）而不是 agents.create：
   * 会话头里带 agentPreset，工具和提示词才按预设装配出来。
   * 用 agents.create 建的会话没有预设 ⇒ 模型只能看见 working_directory 一个工具。
   */
  async function createSession(sc) {
    const cwd = await resolveCwd()
    const request = { cwd }
    if (cfg.agentPreset) request.agentPreset = cfg.agentPreset
    try {
      const out = await sc.create(request)
      return out?.sessionId
    } catch (e) {
      // 别人机器上可能没有这个预设：退回 profile 的默认预设，别因为一个名字就整条通道不可用。
      if (!request.agentPreset) throw e
      log('agentPreset 不可用，改用 profile 默认：', String(e?.message ?? e))
      const out = await sc.create({ cwd })
      return out?.sessionId
    }
  }

  /**
   * 把车道的权限档位切到 cfg.sandboxMode 对应的那一档。
   *
   * 为什么必须切：车道会话默认是 workspace-write，而 dsh-ops 在 workspace-write
   * 下每次执行工具前要先跑 prepare-workspace.ps1 给目录加 ACL；这台机器的
   * PowerShell 执行策略是 Restricted，那个脚本一定被拒 ⇒ pwsh/bash 全都起不来
   * （报 "DSH workspace preparation failed (not a user permission denial)"）。
   * 切到 danger-full-access 后 prep 不再触发，shell 才可用。
   *
   * 优先走宿主自己的 permissionPresets（catalog + set，内部会写 sandbox/mode
   * 和 approval/policy 两个旋钮事件），拿不到再退回 sandbox-policy 的 setSandboxMode。
   */
  let catalogLogged = false

  async function setSandbox(agent) {
    const session = agent?.session
    if (!session) return
    const pp = ctx.get('permissionPresets')
    try {
      if (pp && typeof pp.set === 'function' && typeof pp.catalog === 'function') {
        if (!catalogLogged) {
          catalogLogged = true
          try {
            const cat = pp.catalog()
            log('preset catalog', JSON.stringify((cat?.options ?? []).map(o => o.value)), 'default=', cat?.defaultPreset)
          } catch (e) { log('catalog failed', String(e?.message ?? e)) }
        }
        const want = pickPresetFor(pp, cfg.sandboxMode)
        if (want) {
          pp.set(session, want)
          log('permission preset ->', want, 'for', session.id)
          return
        }
        log('permission preset: no match for', cfg.sandboxMode)
      }
    } catch (e) { log('permissionPresets failed', String(e?.message ?? e)) }
    try {
      const mod = await import('@deepseek-ai/dsh-sandbox-policy')
      if (typeof mod.setSandboxMode === 'function') mod.setSandboxMode(session, cfg.sandboxMode)
    } catch { /* 两条路都拿不到就用 profile 默认 */ }
  }

  function pickPresetFor(pp, sandboxMode) {
    try {
      const cat = pp.catalog()
      for (const opt of cat?.options ?? []) {
        try {
          const spec = pp.resolve(opt.value)
          if (spec?.sandbox === sandboxMode) return opt.value
        } catch { /* 这一档解析不了就跳过 */ }
      }
    } catch { /* catalog 不可用 */ }
    return null
  }

  async function createAgentViaAgents(sessionId) {
    const agents = ctx.get('agents')
    if (!agents) throw new Error('agents 服务不可用')
    const agentOptions = {}
    if (cfg.provider) agentOptions.provider = cfg.provider
    if (cfg.model) agentOptions.model = cfg.model
    if (cfg.reasoningEffort) agentOptions.reasoningEffort = cfg.reasoningEffort
    if (cfg.maxTokens) agentOptions.maxTokens = cfg.maxTokens
    const cwd = await resolveCwd()
    const handle = await agents.create({ sessionId, agentOptions, meta: { cwd } })
    const agent = handle?.agent ?? handle
    await setSandbox(agent)
    return agent
  }

  async function ensureLane() {
    const live = lanes.get('pet')
    if (live?.agent) return live

    const sc = ctx.get('sessionController')
    const agents = ctx.get('agents')
    let sessionId = state.sessions.pet?.id
    let agent = sessionId ? agents?.get?.(sessionId) : undefined
    agent = agent?.agent ?? agent

    if (!agent && sc && typeof sc.create === 'function') {
      sessionId = await createSession(sc)
      state.sessions.pet = { id: sessionId, createdAt: Date.now(), turns: 0 }
      saveState()
      // 会话创建后拿它的 agent 句柄（挂闸 / 等 idle 用）
      try {
        const got = await sc.resolveAgent?.(sessionId)
        agent = got?.agent ?? null
      } catch (e) { log('resolveAgent failed', String(e?.message ?? e)) }
      if (agent) await setSandbox(agent)
    }

    if (!agent) {
      sessionId = sessionId || `pet-${Date.now().toString(36)}`
      agent = await createAgentViaAgents(sessionId)
      state.sessions.pet = { id: sessionId, createdAt: Date.now(), turns: 0 }
      saveState()
    }

    // 复用已有 agent 时也要重新钉一次权限档位（插件重载后 agent 仍在内存里，
    // 但会话的旋钮事件得由我们确保已写进日志）。
    if (agent) await setSandbox(agent)

    const lane = { sessionId, agent, watermark: -1, poller: null, chain: Promise.resolve() }
    lanes.set('pet', lane)
    // 先对齐水位，别把历史整段重放给桌宠
    const events = await readEvents(sessionId)
    lane.watermark = events.reduce((m, ev) => (typeof ev?.seq === 'number' && ev.seq > m ? ev.seq : m), -1)
    attachGate(agent, sessionId)
    log('lane ready', sessionId, 'watermark', lane.watermark)
    push('lane', { sessionId })
    return lane
  }

  async function makeUserMessage(text) {
    const content = [{ type: 'text', text }]
    try {
      const mod = await import('@deepseek-ai/dsh-llm')
      if (typeof mod.createUserMessage === 'function') {
        return mod.createUserMessage({ content, source: { kind: 'user' } })
      }
    } catch { /* 退化成裸对象，DSH 认这个形状 */ }
    return { id: randomUUID(), role: 'user', content, source: { kind: 'user' } }
  }

  // ---------- 事件 → 桌宠帧 ----------
  function frameOf(ev) {
    const d = ev?.data ?? {}
    switch (ev.type) {
      case 'turn/start': return { kind: 'turn-start', turn: d.turn }
      case 'step/start': return { kind: 'step', turn: d.turn, step: d.step }
      case 'tool/call':
        return { kind: 'tool-call', turn: d.turn, step: d.step, callId: d.callId, tool: d.name, args: clip(d.arguments, 1200) }
      case 'tool/result': {
        const text = textOf(d.message?.content)
        return {
          kind: 'tool-result',
          turn: d.turn,
          step: d.step,
          callId: d.message?.toolCallId ?? d.callId,
          ok: !d.error && !d.message?.isError,
          error: d.error?.reason ?? d.error?.name ?? null,
          text: clip(text, 2000),
        }
      }
      case 'assistant/message':
        return { kind: 'assistant', turn: d.turn, step: d.step, text: clip(textOf(d.message?.content), 8000), usage: d.usage ?? null }
      case 'turn/end':
        return {
          kind: 'turn-end',
          turn: d.turn,
          reason: d.reason?.kind ?? 'unknown',
          detail: d.reason ? clip(JSON.stringify(d.reason), 800) : null,
        }
      default: return null
    }
  }

  function startPoller(lane) {
    if (lane.poller) return
    lane.polling = true
    lane.poller = setInterval(async () => {
      const events = await readEvents(lane.sessionId)
      for (const ev of events) {
        if (typeof ev?.seq !== 'number' || ev.seq <= lane.watermark) continue
        lane.watermark = ev.seq
        const frame = frameOf(ev)
        if (frame) push(frame.kind, { ...frame, sessionId: lane.sessionId, seq: ev.seq })
      }
    }, cfg.pollMs)
    lane.poller.unref?.()
  }

  async function stopPoller(lane) {
    if (!lane.poller) return
    clearInterval(lane.poller)
    lane.poller = null
    // 最后再补一次，别丢收尾的那条
    const events = await readEvents(lane.sessionId)
    for (const ev of events) {
      if (typeof ev?.seq !== 'number' || ev.seq <= lane.watermark) continue
      lane.watermark = ev.seq
      const frame = frameOf(ev)
      if (frame) push(frame.kind, { ...frame, sessionId: lane.sessionId, seq: ev.seq })
    }
    push('idle', { sessionId: lane.sessionId })
  }

  // ---------- 跑一轮 ----------
  function submit(text, opts = {}) {
    const key = 'pet'
    const existing = lanes.get(key)
    if (existing) return enqueue(existing, text, opts)
    return ensureLane().then((lane) => enqueue(lane, text, opts))
  }

  function enqueue(lane, text, opts) {
    lane.chain = lane.chain.then(async () => {
      const message = await makeUserMessage(text)
      if (!opts.internal) push('user', { sessionId: lane.sessionId, text: clip(text, 2000) })
      startPoller(lane)
      const t0 = Date.now()
      try {
        await lane.agent.followup(message)
      } catch (e) {
        push('error', { sessionId: lane.sessionId, message: clip(e?.message ?? e, 600) })
        await stopPoller(lane)
        return
      }
      // followup 只代表「收下了」；等它真跑完
      const hb = setInterval(() => {
        push('busy', { sessionId: lane.sessionId, ms: Date.now() - t0 })
      }, 30_000)
      hb.unref?.()
      try {
        await lane.agent.whenIdle?.()
      } catch (e) {
        push('error', { sessionId: lane.sessionId, message: clip(e?.message ?? e, 600) })
      } finally {
        clearInterval(hb)
        await stopPoller(lane)
      }
      const row = state.sessions.pet ?? { id: lane.sessionId, turns: 0 }
      row.turns = (row.turns ?? 0) + 1
      state.sessions.pet = row
      saveState()
      if (cfg.maxTurns > 0 && row.turns >= cfg.maxTurns) {
        log('session hit maxTurns, rolling next turn')
        lanes.delete('pet')
        state.sessions.pet = { id: '', turns: 0 }
        saveState()
      }
    }).catch((e) => log('turn failed', String(e?.message ?? e)))
    return Promise.resolve({ accepted: true, sessionId: lane.sessionId })
  }

  // ---------- HTTP ----------
  const authorized = (req, url) => {
    const h = req.headers['x-pet-token']
    if (typeof h === 'string' && h === token) return true
    return url.searchParams.get('token') === token
  }

  const json = (res, code, body) => {
    const s = JSON.stringify(body)
    try {
      res.writeHead(code, { 'content-type': 'application/json; charset=utf-8', 'content-length': Buffer.byteLength(s) })
      res.end(s)
    } catch { /* 客户端已断 */ }
  }

  const readBody = (req) => new Promise((resolve) => {
    let buf = ''
    req.setEncoding('utf8')
    req.on('data', (c) => {
      buf += c
      if (buf.length > cfg.maxBodyBytes) req.destroy()
    })
    req.on('end', () => {
      try { resolve(buf ? JSON.parse(buf) : {}) } catch { resolve({}) }
    })
    req.on('error', () => resolve({}))
  })

  const server = createServer(async (req, res) => {
    let url
    try { url = new URL(req.url ?? '/', `http://${cfg.host}:${cfg.port}`) } catch { return json(res, 400, { ok: false }) }
    const path = url.pathname
    const lane = lanes.get('pet')

    if (path === '/' || path === '/pet/ping') {
      return json(res, 200, {
        ok: true,
        plugin: 'dsh-pet-channel',
        version: VERSION,
        sessionId: lane?.sessionId ?? null,
        clients: clients.size,
        pending: pending.size,
        approved: state.approved ?? 0,
        rejected: state.rejected ?? 0,
        askApproval: Boolean(cfg.askApproval),
        approvalTimeoutMinutes: Number(cfg.approvalTimeoutMinutes) || 10,
        sandboxMode: cfg.sandboxMode,
        commands: true,
      })
    }

    if (!authorized(req, url)) return json(res, 401, { ok: false, error: 'bad token' })

    if (path === '/pet/debug/presets') {
      const reg = ctx.get('agentPresets') ?? ctx.get('agentPresetRegistry') ?? ctx.get('agentPreset')
      if (!reg) return json(res, 200, { ok: false, error: 'no preset service', keys: Object.keys(ctx.registry?.services ?? {}) })
      let out
      try { out = await (reg.list ? reg.list({}, undefined) : null) } catch (e) { out = { error: String(e?.message ?? e) } }
      return json(res, 200, { ok: true, presets: out })
    }

    if (path === '/pet/debug/perm') {
      const pp = ctx.get('permissionPresets')
      const sp = ctx.get('sandboxPolicy')
      const sess = lane?.agent?.session
      const out = {
        hasPermissionPresets: Boolean(pp),
        hasSandboxPolicy: Boolean(sp),
        sessionId: sess?.id ?? lane?.sessionId ?? null,
        catalog: null,
        specs: [],
        current: null,
        resolved: null,
        keys: Object.keys(ctx.registry?.services ?? {}).filter(k => /perm|sandbox|policy/i.test(k)),
      }
      try { out.catalog = (pp?.catalog?.()?.options ?? []).map(o => ({ value: o.value, name: o.name })) } catch (e) { out.catalog = 'ERR ' + String(e?.message ?? e) }
      for (const opt of (Array.isArray(out.catalog) ? out.catalog : [])) {
        try { out.specs.push([opt.value, pp.resolve(opt.value)]) } catch (e) { out.specs.push([opt.value, 'ERR ' + String(e?.message ?? e)]) }
      }
      if (pp && sess) { try { out.current = pp.current(sess) } catch (e) { out.current = 'ERR ' + String(e?.message ?? e) } }
      if (sp && sess) { try { out.resolved = sp.resolve({ session: sess }) } catch (e) { out.resolved = 'ERR ' + String(e?.message ?? e) } }
      return json(res, 200, { ok: true, ...out })
    }

    if (path === '/pet/events') {
      if (req.method !== 'GET') return json(res, 405, { ok: false })
      res.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-cache',
        connection: 'keep-alive',
      })
      res.write(': pet-channel\n\n')
      res.write(`data: ${JSON.stringify({ kind: 'hello', sessionId: lane?.sessionId ?? null, version: VERSION })}\n\n`)
      clients.add(res)
      const ka = setInterval(() => { try { res.write(': ka\n\n') } catch { /* noop */ } }, 15_000)
      ka.unref?.()
      req.on('close', () => {
        clearInterval(ka)
        clients.delete(res)
      })
      return
    }

    if (path === '/pet/prompt' && req.method === 'POST') {
      const body = await readBody(req)
      const text = String(body.text ?? '').trim()
      if (!text) return json(res, 400, { ok: false, error: 'empty text' })
      try {
        const out = await submit(text)
        return json(res, 200, { ok: true, ...out })
      } catch (e) {
        log('prompt failed', String(e?.message ?? e))
        return json(res, 500, { ok: false, error: String(e?.message ?? e) })
      }
    }

    if (path === '/pet/answer' && req.method === 'POST') {
      const body = await readBody(req)
      const ok = answer(String(body.id ?? ''), Boolean(body.allow))
      return json(res, ok ? 200 : 404, { ok })
    }

    if (path === '/pet/cancel' && req.method === 'POST') {
      if (!lane) return json(res, 404, { ok: false, error: 'no lane' })
      try {
        lane.agent.cancel?.(new Error('pet-channel: user cancel'))
      } catch (e) { log('cancel failed', String(e?.message ?? e)) }
      for (const [id, p] of pending) {
        pending.delete(id)
        clearTimeout(p.timer)
        push('approval-resolved', { id, allow: false, cancelled: true, tool: p.tool })
      }
      return json(res, 200, { ok: true })
    }

    if (path === '/pet/history' && req.method === 'GET') {
      if (!lane) return json(res, 200, { ok: true, items: [] })
      const limit = Math.max(1, Math.min(50, Number(url.searchParams.get('limit') ?? 12)))
      const events = await readEvents(lane.sessionId)
      const items = []
      for (const ev of events) {
        if (ev?.type === 'user/message') {
          const t = textOf(ev.data?.content ?? ev.data?.message?.content)
          if (t) items.push({ role: 'user', text: clip(t, 1000), seq: ev.seq })
        } else if (ev?.type === 'assistant/message') {
          const t = textOf(ev.data?.message?.content)
          if (t) items.push({ role: 'assistant', text: clip(t, 2000), seq: ev.seq })
        }
      }
      return json(res, 200, { ok: true, items: items.slice(-limit) })
    }

    return json(res, 404, { ok: false, error: 'not found' })
  })

  server.on('error', (e) => {
    if (e?.code === 'EADDRINUSE') log(`port ${cfg.port} already in use —— 旧进程还占着？`)
    else log('http server error', String(e?.message ?? e))
  })

  // ---------- 起 / 收 ----------
  ctx.effect(() => {
    server.listen(cfg.port, cfg.host, () => {
      const info = {
        version: VERSION,
        host: cfg.host,
        port: cfg.port,
        origin: `http://${cfg.host}:${cfg.port}`,
        token,
        pid: process.pid,
        startedAt: Date.now(),
        askApproval: Boolean(cfg.askApproval),
      }
      try {
        mkdirSync(dirname(infoFile), { recursive: true })
        writeFileSync(infoFile, JSON.stringify(info, null, 2), 'utf8')
        log('listening', info.origin, '->', infoFile)
      } catch (e) { log('write info failed', String(e?.message ?? e)) }
    })

    return () => {
      for (const res of clients) { try { res.end() } catch { /* noop */ } }
      clients.clear()
      for (const lane of lanes.values()) {
        if (lane.poller) clearInterval(lane.poller)
        lane.poller = null
      }
      return new Promise((r) => server.close(() => r()))
    }
  })

  log('loaded', `${cfg.host}:${cfg.port}`, 'askApproval=', cfg.askApproval, 'sandbox=', cfg.sandboxMode)
}


/** 给模型注册一个 pet_control 工具；拿不到 tools 服务就只留斜杠命令。 */
async function registerPetControl(ctx, write, log) {
  const tools = ctx?.tools
  if (!tools || typeof tools.register !== 'function') {
    log('tools service unavailable; pet_control disabled')
    return
  }
  let defineTool = (definition) => definition
  try {
    const mod = await import('@deepseek-ai/dsh-tools')
    if (typeof mod.defineTool === 'function') defineTool = mod.defineTool
  } catch { /* 没有导出就用裸定义；register 只要求字段齐全 */ }
  try {
    tools.register(
      defineTool({
        name: 'pet_control',
        description:
          '控制桌面宠物：say（说话）、play_animation（播放动作）、notify（提醒）。'
          + '动作名必须是桌宠当前角色已有的动画；不确定就先 say。',
        parameters: {
          action: {
            type: 'string',
            description: 'say | play_animation | notify',
          },
          text: { type: 'string', description: 'say/notify 的内容，最多 200 字' },
          animation: { type: 'string', description: 'play_animation 的动作名' },
          duration_ms: { type: 'number', description: '气泡停留毫秒，最多 10000' },
        },
        output: {
          schema: {
            type: 'object',
            properties: { ok: { type: 'boolean', required: true } },
          },
          render: (_args, value) => [
            { type: 'text', text: value?.ok ? '已发送给桌宠' : '被限流或参数不合法' },
          ],
        },
        async execute(args) {
          const ok = write(String(args?.action ?? ''), {
            text: args?.text,
            name: args?.animation,
            duration_ms: args?.duration_ms,
          })
          return { ok: Boolean(ok) }
        },
      }),
    )
    log('pet_control registered')
  } catch (e) {
    log('pet_control register failed', String(e?.message ?? e))
  }
}
