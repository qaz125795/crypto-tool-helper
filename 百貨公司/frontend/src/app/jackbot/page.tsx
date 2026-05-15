'use client'
import React, { useState, useEffect, useCallback, useRef } from 'react'

// ─── 型別 ──────────────────────────────────────────────────────────────────
interface Job {
  id: string
  name: string
  enabled: boolean
  cron: string
  next_run_tw: string | null
  has_params: boolean
}

interface ParamItem {
  key: string
  name: string
  type: string
  default: string
  current: string
}

interface TaskLog {
  task: string
  status: string
  last_run: string | null
  logs: string[]
}

// ─── 分區設定 ──────────────────────────────────────────────────────────────
const GROUPS = [
  {
    key: 'signal',
    label: '🎯 策略進場',
    desc: '高優先訊號 · 每輪最多推 1-3 筆',
    ids: ['crit_radar', 'position_change', 'gold_signal', 'altseason_radar'],
    accent: 'text-blue-400',
    border: 'border-blue-500/20',
    bg: 'bg-blue-500/5',
  },
  {
    key: 'market',
    label: '📊 市場監控',
    desc: '流動性 · 資金費率 · 牛熊燃料',
    ids: ['liquidity_radar', 'hyperliquid', 'funding_rate', 'buying_power_monitor', 'screener_board', 'sector_ranking'],
    accent: 'text-emerald-400',
    border: 'border-emerald-500/20',
    bg: 'bg-emerald-500/5',
  },
  {
    key: 'macro',
    label: '🌍 宏觀與新聞',
    desc: '經濟日曆 · 新聞快訊 · 長線指標',
    ids: ['news', 'economic_data', 'economic_data_preview', 'long_term_index'],
    accent: 'text-purple-400',
    border: 'border-purple-500/20',
    bg: 'bg-purple-500/5',
  },
]

// ─── helpers ───────────────────────────────────────────────────────────────
function getToken() {
  if (typeof document === 'undefined') return ''
  return document.cookie.split('; ').find(r => r.startsWith('admin_token='))?.split('=')[1] ?? ''
}

/** 相對時間（Safari 相容：空格→T） */
function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '--'
  try {
    const date = new Date(dateStr.replace(' ', 'T'))
    if (isNaN(date.getTime())) return dateStr.slice(0, 16)
    const diff = Math.floor((Date.now() - date.getTime()) / 1000)
    if (diff < 60)    return '剛剛'
    if (diff < 3600)  return `${Math.floor(diff / 60)} 分鐘前`
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`
    return `${Math.floor(diff / 86400)} 天前`
  } catch { return dateStr.slice(0, 16) }
}

/** Cron 轉中文（白話文） */
function formatCronToChinese(cron: string): string {
  const p = cron.trim().split(/\s+/)
  if (p.length !== 5) return cron
  const [min, hr, , , dow] = p
  if (min === '*/5'  && hr === '*') return '每 5 分鐘'
  if (min === '*/10' && hr === '*') return '每 10 分鐘'
  if (min === '*/15' && hr === '*') return '每 15 分鐘'
  if (min === '*/30' && hr === '*') return '每 30 分鐘'
  if (min === '0'    && hr === '*') return '每小時整點'
  if (min === '25'   && hr === '*') return '每小時 :25'
  if (min === '20'   && hr === '*') return '每小時 :20'
  if (min === '35'   && hr === '*') return '每小時 :35'
  if (min === '50'   && hr === '*') return '每小時 :50'
  if (min === '10'   && hr === '0') return '每日 08:10（台北）'
  if (min === '0'    && hr === '1') return '每日凌晨 09:00（台北）'
  if (min === '55'   && hr.includes(',')) return `每日 ${hr.split(',').map(h => `${(parseInt(h)+8)%24}`).join('/')} 點 :55`
  if (hr !== '*' && min !== '*')   return `每日 ${(parseInt(hr)+8)%24}:${min.padStart(2,'0')}（台北）`
  return cron
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T | null> {
  try {
    const res = await fetch(`/api/jackbot${path}`, {
      method,
      headers: { Authorization: `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.status === 401) { window.location.href = '/login'; return null }
    if (!res.ok) return null
    return res.json()
  } catch { return null }
}

// ─── 子元件 ────────────────────────────────────────────────────────────────
function Toast({ msg, type }: { msg: string; type: 'ok' | 'err' }) {
  return (
    <div className={`fixed bottom-20 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-xl shadow-lg text-sm font-medium ${
      type === 'ok' ? 'bg-green-500/90 text-white' : 'bg-red-500/90 text-white'
    }`}>
      {msg}
    </div>
  )
}

function Spinner() {
  return (
    <svg className="animate-spin h-3.5 w-3.5 inline-block" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
    </svg>
  )
}

// ─── 訊號卡片 ───────────────────────────────────────────────────────────────
function JobCard({
  job, log, busy, expandLog,
  onRun, onToggle, onEditCron, onLoadParams, onToggleLog,
}: {
  job: Job
  log: TaskLog | undefined
  busy: string | null
  expandLog: boolean
  onRun: () => void
  onToggle: () => void
  onEditCron: () => void
  onLoadParams: () => void
  onToggleLog: () => void
}) {
  const st = log?.status
  const isError   = st === 'error'
  const isRunning = st === 'running'
  const isBusy    = busy === `run-${job.id}` || busy === `toggle-${job.id}`

  const badge = (() => {
    if (!job.enabled) return { cls: 'bg-gray-700/60 text-gray-400 border-gray-600/30', label: '⏸ 已暫停' }
    if (isRunning)   return { cls: 'bg-yellow-400/20 text-yellow-300 border-yellow-400/40 animate-pulse', label: '🟢 掃描中' }
    if (isError)     return { cls: 'bg-red-500/20 text-red-400 border-red-500/40', label: '🔴 異常' }
    if (st === 'success') return { cls: 'bg-green-500/15 text-green-400 border-green-500/30', label: '✅ 就緒' }
    return { cls: 'bg-gray-700/50 text-gray-400 border-gray-600/30', label: '⏳ 待命起跑' }
  })()

  return (
    <div className={`bg-gray-800/80 border rounded-xl overflow-hidden transition-all ${
      isError ? 'border-red-500/50 shadow-red-500/10 shadow-md' : 'border-gray-700/50'
    }`}>
      <div className="p-4">
        {/* 標題行 */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-white">{job.name}</span>
              <span className={`text-[10px] px-2 py-0.5 rounded-full border ${badge.cls}`}>
                {badge.label}
              </span>
            </div>
            {/* Cron 白話文 + 原始 */}
            <div className="flex items-center gap-2 mt-1">
              <span className="text-xs text-gray-300">{formatCronToChinese(job.cron)}</span>
              <span className="text-[10px] text-gray-600 font-mono">{job.cron}</span>
            </div>
            {/* 下次 / 上次 */}
            <div className="flex gap-3 mt-0.5">
              {job.next_run_tw && (
                <span className="text-[10px] text-gray-500">下次 {job.next_run_tw}</span>
              )}
              {log?.last_run && (
                <span className={`text-[10px] ${isError ? 'text-red-400/70' : 'text-gray-500'}`}>
                  上次 {timeAgo(log.last_run)}
                </span>
              )}
            </div>
          </div>
          {/* Toggle */}
          <button
            onClick={onToggle}
            disabled={isBusy}
            title={job.enabled ? '暫停排程' : '恢復排程'}
            className={`shrink-0 w-11 h-6 rounded-full relative transition-colors disabled:opacity-50 ${
              job.enabled ? 'bg-green-500 hover:bg-green-400' : 'bg-gray-600 hover:bg-gray-500'
            }`}
          >
            <span className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow transition-all ${
              job.enabled ? 'left-6' : 'left-1'
            }`}/>
          </button>
        </div>

        {/* 按鈕列 */}
        <div className="flex gap-2 flex-wrap mt-3">
          {/* Primary：手動執行 */}
          <button
            onClick={onRun}
            disabled={busy === `run-${job.id}`}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {busy === `run-${job.id}` ? <><Spinner /> 啟動中</> : '▶ 手動執行'}
          </button>
          {/* Secondary：修改排程 */}
          <button
            onClick={onEditCron}
            className="text-xs px-3 py-1.5 border border-gray-600/60 text-gray-300 rounded-lg hover:bg-gray-700/60 transition-colors"
          >
            ⏱ 修改排程
          </button>
          {/* Secondary：調整參數 */}
          {job.has_params && (
            <button
              onClick={onLoadParams}
              className="text-xs px-3 py-1.5 border border-purple-500/40 text-purple-400 rounded-lg hover:bg-purple-500/10 transition-colors"
            >
              ⚙ 調整參數
            </button>
          )}
          {/* 日誌 */}
          <button
            onClick={onToggleLog}
            className={`text-xs px-3 py-1.5 border rounded-lg ml-auto transition-colors ${
              isError
                ? 'border-red-500/40 text-red-400 hover:bg-red-500/10'
                : 'border-gray-600/40 text-gray-400 hover:bg-gray-700/50'
            }`}
          >
            {expandLog ? '▲ 收起' : (isError ? '🔍 查看錯誤' : '▼ 日誌')}
          </button>
        </div>
      </div>

      {/* 錯誤提示橫幅 */}
      {isError && !expandLog && (
        <div className="border-t border-red-500/30 bg-red-950/30 px-4 py-2 flex items-center gap-2">
          <span className="text-[10px] text-red-400">⚠️</span>
          <span className="text-[11px] text-red-300 flex-1 truncate">
            {log?.logs?.at(-1) ?? '上次執行失敗，點擊「查看錯誤」查看詳情'}
          </span>
        </div>
      )}

      {/* 日誌展開區 */}
      {expandLog && (
        <div className={`border-t p-3 font-mono text-[10px] max-h-48 overflow-y-auto ${
          isError
            ? 'border-red-500/30 bg-red-950/20 text-red-200'
            : 'border-gray-700/40 bg-gray-900/60 text-gray-400'
        }`}>
          {log?.logs && log.logs.length > 0 ? (
            log.logs.slice(-15).map((line, i) => (
              <div key={i} className="leading-relaxed whitespace-pre-wrap break-all py-0.5">{line}</div>
            ))
          ) : (
            <span className="text-gray-600">尚無日誌（重啟後重置）</span>
          )}
        </div>
      )}
    </div>
  )
}

// ─── 主頁面 ────────────────────────────────────────────────────────────────
export default function JackBotAdmin() {
  const [jobs,       setJobs]      = useState<Job[]>([])
  const [logs,       setLogs]      = useState<Record<string, TaskLog>>({})
  const [loading,    setLoading]   = useState(true)
  const [toast,      setToast]     = useState<{ msg: string; type: 'ok' | 'err' } | null>(null)
  const [activeTab,  setActiveTab] = useState('signal')

  const [editCron,   setEditCron]  = useState<{ id: string; val: string } | null>(null)
  const [params,     setParams]    = useState<{ id: string; items: ParamItem[] } | null>(null)
  const [paramEdits, setParamEdits]= useState<Record<string, string>>({})
  const [expandLog,  setExpandLog] = useState<Record<string, boolean>>({})
  const [busy,       setBusy]      = useState<string | null>(null)
  const [, setTick] = useState(0)

  const pollIntervals = useRef<Set<ReturnType<typeof setInterval>>>(new Set())

  const showToast = (msg: string, type: 'ok' | 'err' = 'ok') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 2800)
  }

  const loadAll = useCallback(async () => {
    const [jobsData, allLogs] = await Promise.all([
      req<Job[]>('GET', '/api/admin/jobs'),
      req<Record<string, TaskLog>>('GET', '/api/logs'),
    ])
    if (jobsData) setJobs(jobsData)
    if (allLogs)  setLogs(allLogs)
    setLoading(false)
  }, [])

  useEffect(() => {
    if (!getToken()) { window.location.href = '/login'; return }
    void loadAll()
    const iv     = setInterval(() => void loadAll(), 10000)
    const tickIv = setInterval(() => setTick(t => t + 1), 60000)
    return () => {
      clearInterval(iv)
      clearInterval(tickIv)
      pollIntervals.current.forEach(clearInterval)
      pollIntervals.current.clear()
    }
  }, [loadAll])

  // ── 動作 ──────────────────────────────────────────────────────────────────
  const toggleJob = async (job: Job) => {
    if (job.enabled && !window.confirm(
      `確定要暫停「${job.name}」？\n暫停後訊號不再自動推播，需手動恢復。`
    )) return
    setBusy(`toggle-${job.id}`)
    const path = job.enabled
      ? `/api/admin/jobs/${job.id}/pause`
      : `/api/admin/jobs/${job.id}/resume`
    const r = await req<{ ok: boolean }>('POST', path)
    if (r?.ok) showToast(job.enabled ? `⏸ ${job.name} 已暫停` : `▶ ${job.name} 已恢復`)
    else showToast('操作失敗', 'err')
    await loadAll()
    setBusy(null)
  }

  const saveCron = async () => {
    if (!editCron) return
    const parts = editCron.val.trim().split(/\s+/)
    if (parts.length !== 5) {
      showToast('Cron 必須剛好 5 個欄位（分 時 日 月 週）', 'err')
      return
    }
    setBusy('cron')
    const r = await req<{ ok: boolean }>('PUT', `/api/admin/jobs/${editCron.id}/cron`, { cron: editCron.val })
    if (r?.ok) { showToast('✅ 排程已更新'); setEditCron(null) }
    else showToast('Cron 格式錯誤', 'err')
    await loadAll()
    setBusy(null)
  }

  const loadParams = async (id: string) => {
    const r = await req<{ task: string; items: ParamItem[] }>('GET', `/api/admin/params/${id}`)
    if (r) {
      setParams({ id: r.task, items: r.items })
      const map: Record<string, string> = {}
      r.items.forEach(it => { map[it.key] = it.current })
      setParamEdits(map)
    }
  }

  const saveParams = async () => {
    if (!params) return
    setBusy('params')
    const r = await req<{ ok: boolean }>('PUT', `/api/admin/params/${params.id}`, { updates: paramEdits })
    if (r?.ok) { showToast('✅ 參數已更新'); setParams(null) }
    else showToast('更新失敗', 'err')
    setBusy(null)
  }

  const runTask = async (id: string) => {
    setBusy(`run-${id}`)
    const r = await req<{ status: string }>('POST', `/run/${id}`)
    if (r?.status === 'accepted' || r?.status === 'success') {
      showToast('🚀 任務已在背景啟動')
      let poll = 0
      const iv = setInterval(async () => {
        poll++
        await loadAll()
        const fresh = await req<TaskLog>('GET', `/api/logs/${id}`)
        if (poll > 15 || (fresh?.status && fresh.status !== 'running' && fresh.status !== 'pending')) {
          clearInterval(iv)
          pollIntervals.current.delete(iv)
        }
      }, 4000)
      pollIntervals.current.add(iv)
    } else {
      showToast('執行失敗', 'err')
    }
    setBusy(null)
  }

  const toggleLog = async (id: string) => {
    const open = !expandLog[id]
    setExpandLog(prev => ({ ...prev, [id]: open }))
    if (open) {
      const r = await req<TaskLog>('GET', `/api/logs/${id}`)
      if (r) setLogs(prev => ({ ...prev, [id]: r }))
    }
  }

  // ── 統計 ──────────────────────────────────────────────────────────────────
  const errorCount   = jobs.filter(j => logs[j.id]?.status === 'error').length
  const runningCount = jobs.filter(j => logs[j.id]?.status === 'running').length

  const activeGroup = GROUPS.find(g => g.key === activeTab) ?? GROUPS[0]
  const visibleJobs = jobs.filter(j => activeGroup.ids.includes(j.id))

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="flex items-center gap-2 text-gray-400 text-sm">
          <Spinner /> 載入指揮中心...
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-20 bg-gray-900/95 backdrop-blur border-b border-gray-800">
        <div className="px-4 py-3 flex items-center gap-3">
          <button
            onClick={() => window.history.back()}
            className="text-gray-400 hover:text-white text-sm px-2 py-1 rounded-lg hover:bg-gray-800 transition-colors"
          >
            ← 返回
          </button>
          <div className="flex-1">
            <h1 className="text-base font-bold text-white">⚡ 訊號指揮中心</h1>
            <div className="flex items-center gap-3 mt-0.5">
              <span className="text-[11px] text-gray-500">{jobs.length} 支訊號</span>
              {runningCount > 0 && (
                <span className="text-[10px] text-yellow-400 animate-pulse">
                  🟡 {runningCount} 掃描中
                </span>
              )}
              {errorCount > 0 && (
                <span className="text-[10px] text-red-400">
                  🔴 {errorCount} 異常
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => void loadAll()}
            className="text-[11px] text-gray-500 hover:text-gray-300 px-2 py-1 rounded-lg hover:bg-gray-800 transition-colors"
          >
            ↻ 重整
          </button>
        </div>

        {/* ── Tab 導覽 ─────────────────────────────────────────────────── */}
        <div className="flex border-t border-gray-800/60 overflow-x-auto">
          {GROUPS.map(g => {
            const groupErrors = jobs.filter(
              j => g.ids.includes(j.id) && logs[j.id]?.status === 'error'
            ).length
            return (
              <button
                key={g.key}
                onClick={() => setActiveTab(g.key)}
                className={`flex-1 min-w-0 px-3 py-2.5 text-xs font-medium transition-all whitespace-nowrap relative ${
                  activeTab === g.key
                    ? `${g.accent} border-b-2 border-current bg-gray-800/40`
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {g.label}
                {groupErrors > 0 && (
                  <span className="ml-1 w-4 h-4 inline-flex items-center justify-center bg-red-500 text-white text-[9px] rounded-full">
                    {groupErrors}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </header>

      {/* ── 分區描述 ──────────────────────────────────────────────────────── */}
      <div className={`mx-4 mt-4 mb-3 px-4 py-2.5 rounded-xl border ${activeGroup.border} ${activeGroup.bg}`}>
        <p className={`text-xs font-medium ${activeGroup.accent}`}>{activeGroup.label}</p>
        <p className="text-[11px] text-gray-400 mt-0.5">{activeGroup.desc}</p>
      </div>

      {/* ── 訊號卡片列表 ─────────────────────────────────────────────────── */}
      <main className="px-4 pb-8 space-y-3">
        {visibleJobs.length === 0 ? (
          <div className="text-center py-12 text-gray-600 text-sm">此分區無訊號任務</div>
        ) : (
          visibleJobs.map(job => (
            <JobCard
              key={job.id}
              job={job}
              log={logs[job.id]}
              busy={busy}
              expandLog={!!expandLog[job.id]}
              onRun={() => void runTask(job.id)}
              onToggle={() => void toggleJob(job)}
              onEditCron={() => setEditCron({ id: job.id, val: job.cron })}
              onLoadParams={() => void loadParams(job.id)}
              onToggleLog={() => void toggleLog(job.id)}
            />
          ))
        )}
      </main>

      {/* ── Cron 編輯 Modal ──────────────────────────────────────────────── */}
      {editCron && (
        <div
          className="fixed inset-0 z-50 bg-black/70 flex items-end justify-center p-4"
          onClick={() => setEditCron(null)}
        >
          <div
            className="bg-gray-800 border border-gray-700 rounded-2xl p-5 w-full max-w-sm"
            onClick={e => e.stopPropagation()}
          >
            <h3 className="font-semibold text-white mb-0.5">⏱ 修改觸發排程</h3>
            <p className="text-[11px] text-gray-500 mb-1">
              目前白話文：<span className="text-blue-400">{formatCronToChinese(editCron.val)}</span>
            </p>
            <p className="text-[10px] text-gray-600 mb-3">格式：分 時 日 月 週（共 5 個欄位）</p>
            <input
              className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5 text-sm font-mono text-white focus:outline-none focus:border-blue-500"
              value={editCron.val}
              onChange={e => setEditCron(p => p ? { ...p, val: e.target.value } : null)}
              placeholder="*/15 * * * *"
            />
            <p className="text-[10px] text-blue-400/70 mt-1 min-h-[14px]">
              {editCron.val.trim().split(/\s+/).length === 5
                ? `→ ${formatCronToChinese(editCron.val)}`
                : '請輸入 5 個欄位'}
            </p>
            <div className="grid grid-cols-2 gap-2 mt-3">
              <button
                onClick={() => setEditCron(null)}
                className="py-2.5 bg-gray-700 text-gray-300 rounded-lg text-sm hover:bg-gray-600"
              >取消</button>
              <button
                onClick={() => void saveCron()}
                disabled={busy === 'cron'}
                className="py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50"
              >
                {busy === 'cron' ? <><Spinner /> 儲存中</> : '儲存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── 參數編輯 Modal ───────────────────────────────────────────────── */}
      {params && (
        <div
          className="fixed inset-0 z-50 bg-black/70 flex items-end justify-center p-4 overflow-y-auto"
          onClick={() => setParams(null)}
        >
          <div
            className="bg-gray-800 border border-gray-700 rounded-2xl p-5 w-full max-w-sm my-4"
            onClick={e => e.stopPropagation()}
          >
            <h3 className="font-semibold text-white mb-0.5">
              ⚙ {jobs.find(j => j.id === params.id)?.name ?? params.id} 參數
            </h3>
            <p className="text-[11px] text-gray-500 mb-4">修改後即時生效，重啟後保留</p>
            <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
              {params.items.map(it => (
                <div key={it.key}>
                  <label className="text-xs text-gray-300 block mb-1 font-medium">
                    {it.name}
                    <span className="ml-1.5 text-gray-600 font-mono text-[10px]">{it.key}</span>
                  </label>
                  {it.type === 'bool' ? (
                    <select
                      className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white"
                      value={paramEdits[it.key] ?? it.current}
                      onChange={e => setParamEdits(p => ({ ...p, [it.key]: e.target.value }))}
                    >
                      <option value="true">✅ 開啟 (true)</option>
                      <option value="false">⏸ 關閉 (false)</option>
                    </select>
                  ) : (
                    <input
                      type={it.type === 'int' || it.type === 'float' ? 'number' : 'text'}
                      step={it.type === 'float' ? '0.1' : '1'}
                      className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-purple-500"
                      value={paramEdits[it.key] ?? it.current}
                      onChange={e => setParamEdits(p => ({ ...p, [it.key]: e.target.value }))}
                    />
                  )}
                  <p className="text-[10px] text-gray-600 mt-0.5">預設：{it.default}</p>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2 mt-4">
              <button
                onClick={() => setParams(null)}
                className="py-2.5 bg-gray-700 text-gray-300 rounded-lg text-sm hover:bg-gray-600"
              >取消</button>
              <button
                onClick={() => void saveParams()}
                disabled={busy === 'params'}
                className="py-2.5 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-500 disabled:opacity-50"
              >
                {busy === 'params' ? <><Spinner /> 儲存中</> : '儲存參數'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast msg={toast.msg} type={toast.type} />}
    </div>
  )
}
