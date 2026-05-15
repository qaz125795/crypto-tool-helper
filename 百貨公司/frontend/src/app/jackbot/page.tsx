'use client'
import React, { useState, useEffect, useCallback } from 'react'

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

// ─── helpers ───────────────────────────────────────────────────────────────
function getToken() {
  if (typeof document === 'undefined') return ''
  return document.cookie.split('; ').find(r => r.startsWith('admin_token='))?.split('=')[1] ?? ''
}

/** 相對時間顯示（"剛剛" / "3 分鐘前" / "2 小時前"）*/
function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '--'
  try {
    const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000)
    if (diff < 60)  return '剛剛'
    if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`
    return `${Math.floor(diff / 86400)} 天前`
  } catch { return dateStr.slice(0, 16) }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T | null> {
  try {
    const res = await fetch(`/api/jackbot${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${getToken()}`,
        'Content-Type': 'application/json',
      },
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
    <div className={`fixed bottom-20 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-xl shadow-lg text-sm font-medium transition-all ${
      type === 'ok' ? 'bg-green-500/90 text-white' : 'bg-red-500/90 text-white'
    }`}>
      {msg}
    </div>
  )
}

// ─── 主頁面 ────────────────────────────────────────────────────────────────
export default function JackBotAdmin() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [logs, setLogs] = useState<Record<string, TaskLog>>({})
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState<{ msg: string; type: 'ok' | 'err' } | null>(null)

  // 編輯狀態
  const [editCron, setEditCron] = useState<{ id: string; val: string } | null>(null)
  const [params, setParams] = useState<{ id: string; items: ParamItem[] } | null>(null)
  const [paramEdits, setParamEdits] = useState<Record<string, string>>({})
  const [expandLog, setExpandLog] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const showToast = (msg: string, type: 'ok' | 'err' = 'ok') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 2500)
  }

  const loadAll = useCallback(async () => {
    const [jobsData, allLogs] = await Promise.all([
      req<Job[]>('GET', '/api/admin/jobs'),
      req<Record<string, TaskLog>>('GET', '/api/logs'),
    ])
    if (jobsData) setJobs(jobsData)
    if (allLogs) setLogs(allLogs)
    setLoading(false)
  }, [])

  useEffect(() => {
    if (!getToken()) { window.location.href = '/login'; return }
    void loadAll()
    const iv = setInterval(() => void loadAll(), 10000)
    return () => clearInterval(iv)
  }, [loadAll])

  // ── 動作 ────────────────────────────────────────────────────────────────
  const toggleJob = async (job: Job) => {
    // 暫停前二次確認（防誤觸）
    if (job.enabled && !window.confirm(`確定要暫停「${job.name}」排程？\n暫停後訊號將不再自動推播，需手動恢復。`)) return
    setBusy(`toggle-${job.id}`)
    const path = job.enabled ? `/api/admin/jobs/${job.id}/pause` : `/api/admin/jobs/${job.id}/resume`
    const r = await req<{ ok: boolean }>('POST', path)
    if (r?.ok) showToast(job.enabled ? `⏸ ${job.name} 已暫停` : `▶ ${job.name} 已恢復`)
    else showToast('操作失敗', 'err')
    await loadAll()
    setBusy(null)
  }

  const saveCron = async () => {
    if (!editCron) return
    setBusy('cron')
    const r = await req<{ ok: boolean }>('PUT', `/api/admin/jobs/${editCron.id}/cron`, { cron: editCron.val })
    if (r?.ok) { showToast('Cron 已更新'); setEditCron(null) }
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
    if (r?.ok) { showToast('參數已更新'); setParams(null) }
    else showToast('更新失敗', 'err')
    setBusy(null)
  }

  const runTask = async (id: string) => {
    setBusy(`run-${id}`)
    const r = await req<{ status: string }>('POST', `/run/${id}`)
    if (r?.status === 'accepted' || r?.status === 'success') {
      showToast('🚀 任務已在背景啟動')
      // 啟動 polling：每 4 秒更新日誌，直到 running → success/error
      let poll = 0
      const iv = setInterval(async () => {
        poll++
        await loadAll()
        const log = logs[id]
        if (poll > 15 || (log?.status && log.status !== 'running')) clearInterval(iv)
      }, 4000)
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

  // ─── 渲染 ─────────────────────────────────────────────────────────────
  const statusBadge = (job: Job) => {
    const tl = logs[job.id]
    const runStatus = tl?.status
    if (!job.enabled)
      return { cls: 'bg-gray-700/60 text-gray-400 border-gray-600/30', label: '⏸ 已暫停', pulse: false }
    if (runStatus === 'running')
      return { cls: 'bg-yellow-500/20 text-yellow-300 border-yellow-400/40', label: '🟡 執行中...', pulse: true }
    if (runStatus === 'success')
      return { cls: 'bg-green-500/15 text-green-400 border-green-500/30', label: '✅ 成功', pulse: false }
    if (runStatus === 'error')
      return { cls: 'bg-red-500/15 text-red-400 border-red-500/30', label: '❌ 錯誤', pulse: false }
    // pending：尚未執行過，但已排程（友善提示）
    if (!runStatus && job.next_run_tw)
      return { cls: 'bg-gray-700/50 text-gray-400 border-gray-600/30', label: '⏳ 等待排程觸發', pulse: false }
    return { cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30', label: '✓ 就緒', pulse: false }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-400 text-sm">載入中...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* Header */}
      <header className="sticky top-0 z-10 bg-gray-900/95 backdrop-blur border-b border-gray-800 px-4 py-3 flex items-center gap-3">
        <button
          onClick={() => window.history.back()}
          className="text-gray-400 hover:text-white transition-colors text-sm px-2 py-1 rounded-lg hover:bg-gray-800"
        >
          ← 返回
        </button>
        <div>
          <h1 className="text-base font-bold text-white">⚡ 一級雷達後台</h1>
          <p className="text-[11px] text-gray-500">{jobs.length} 個訊號任務</p>
        </div>
        <button
          onClick={() => void loadAll()}
          className="ml-auto text-[11px] text-gray-500 hover:text-gray-300 px-2 py-1 rounded-lg hover:bg-gray-800"
        >
          重整
        </button>
      </header>

      <main className="p-4 space-y-3 pb-8">
        {jobs.map(job => {
          const badge = statusBadge(job)
          const tl = logs[job.id]
          const logOpen = expandLog[job.id]
          const isError = tl?.status === 'error'
          return (
            <div key={job.id} className={`bg-gray-800 border rounded-xl overflow-hidden ${
              isError ? 'border-red-500/60' : 'border-gray-700/60'
            }`}>
              {/* 卡片頭 */}
              <div className="p-4">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold text-white">{job.name}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${badge.cls} ${
                        badge.pulse ? 'animate-pulse' : ''
                      }`}>
                        {badge.label}
                      </span>
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5 font-mono">{job.cron}</p>
                    {job.next_run_tw && (
                      <p className="text-[11px] text-gray-600 mt-0.5">
                        下次：{job.next_run_tw}（台灣）
                      </p>
                    )}
                    {tl?.last_run && (
                      <p className={`text-[11px] mt-0.5 ${isError ? 'text-red-400/70' : 'text-gray-600'}`}>
                        上次：{timeAgo(tl.last_run)}
                      </p>
                    )}
                  </div>

                  {/* 啟用/暫停開關 */}
                  <button
                    onClick={() => void toggleJob(job)}
                    disabled={busy === `toggle-${job.id}`}
                    className={`shrink-0 w-12 h-6 rounded-full transition-all relative ${
                      job.enabled ? 'bg-green-500' : 'bg-gray-600'
                    } disabled:opacity-50`}
                  >
                    <span className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-all shadow ${
                      job.enabled ? 'left-7' : 'left-1'
                    }`} />
                  </button>
                </div>

                {/* 動作按鈕列 */}
                <div className="flex gap-2 flex-wrap mt-3">
                  <button
                    onClick={() => void runTask(job.id)}
                    disabled={busy === `run-${job.id}`}
                    className="text-[11px] px-3 py-1.5 bg-blue-500/15 text-blue-400 border border-blue-500/30 rounded-lg hover:bg-blue-500/25 disabled:opacity-50"
                  >
                    {busy === `run-${job.id}` ? '執行中...' : '手動執行'}
                  </button>
                  <button
                    onClick={() => setEditCron({ id: job.id, val: job.cron })}
                    className="text-[11px] px-3 py-1.5 bg-gray-700/60 text-gray-300 border border-gray-600/40 rounded-lg hover:bg-gray-600/60"
                  >
                    修改 Cron
                  </button>
                  {job.has_params && (
                    <button
                      onClick={() => void loadParams(job.id)}
                      className="text-[11px] px-3 py-1.5 bg-purple-500/15 text-purple-400 border border-purple-500/30 rounded-lg hover:bg-purple-500/25"
                    >
                      調整參數
                    </button>
                  )}
                  <button
                    onClick={() => void toggleLog(job.id)}
                    className="text-[11px] px-3 py-1.5 bg-gray-700/60 text-gray-400 border border-gray-600/40 rounded-lg hover:bg-gray-600/60 ml-auto"
                  >
                    {logOpen ? '▲ 收起' : '▼ 查看日誌'}
                  </button>
                </div>
              </div>

              {/* 錯誤提示（自動展開）*/}
              {isError && (
                <div className="border-t border-red-500/30 bg-red-950/30 px-3 py-2 text-[11px] text-red-400">
                  ⚠️ 上次執行失敗，請查看下方日誌排查錯誤。
                </div>
              )}

              {/* 日誌區（可展開/收起）*/}
              {logOpen && (
                <div className={`border-t p-3 font-mono text-[10px] max-h-44 overflow-y-auto ${
                  isError
                    ? 'border-red-500/30 bg-red-950/20 text-red-300'
                    : 'border-gray-700/50 bg-gray-900/60 text-gray-400'
                }`}>
                  {tl?.logs && tl.logs.length > 0 ? (
                    tl.logs.slice(-15).map((line, i) => (
                      <div key={i} className="leading-relaxed whitespace-pre-wrap break-all">{line}</div>
                    ))
                  ) : (
                    <span className="text-gray-600">尚無日誌記錄（重啟後重置）</span>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </main>

      {/* ── Cron 編輯 Modal ────────────────────────────────────────────── */}
      {editCron && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-end justify-center p-4" onClick={() => setEditCron(null)}>
          <div className="bg-gray-800 border border-gray-700 rounded-2xl p-5 w-full max-w-sm" onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold text-white mb-1">修改觸發頻率</h3>
            <p className="text-[11px] text-gray-500 mb-3">
              5 個欄位：分 時 日 月 週（例：*/5 * * * *）
            </p>
            <input
              className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5 text-sm font-mono text-white focus:outline-none focus:border-blue-500"
              value={editCron.val}
              onChange={e => setEditCron(p => p ? { ...p, val: e.target.value } : null)}
            />
            <div className="grid grid-cols-2 gap-2 mt-3">
              <button onClick={() => setEditCron(null)}
                className="py-2.5 bg-gray-700 text-gray-300 rounded-lg text-sm hover:bg-gray-600">
                取消
              </button>
              <button
                onClick={() => void saveCron()}
                disabled={busy === 'cron'}
                className="py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50">
                {busy === 'cron' ? '儲存中...' : '儲存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── 參數編輯 Modal ─────────────────────────────────────────────── */}
      {params && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-end justify-center p-4 overflow-y-auto" onClick={() => setParams(null)}>
          <div className="bg-gray-800 border border-gray-700 rounded-2xl p-5 w-full max-w-sm my-4" onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold text-white mb-1">
              {jobs.find(j => j.id === params.id)?.name ?? params.id} 參數
            </h3>
            <p className="text-[11px] text-gray-500 mb-4">修改後即時生效，重啟後保留</p>
            <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
              {params.items.map(it => (
                <div key={it.key}>
                  <label className="text-xs text-gray-400 block mb-1">
                    {it.name}
                    <span className="ml-1 text-gray-600 font-mono text-[10px]">({it.key})</span>
                  </label>
                  {it.type === 'bool' ? (
                    <select
                      className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white"
                      value={paramEdits[it.key] ?? it.current}
                      onChange={e => setParamEdits(p => ({ ...p, [it.key]: e.target.value }))}
                    >
                      <option value="true">true（開）</option>
                      <option value="false">false（關）</option>
                    </select>
                  ) : (
                    <input
                      type={it.type === 'int' || it.type === 'float' ? 'number' : 'text'}
                      step={it.type === 'float' ? '0.1' : '1'}
                      className="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-white font-mono"
                      value={paramEdits[it.key] ?? it.current}
                      onChange={e => setParamEdits(p => ({ ...p, [it.key]: e.target.value }))}
                    />
                  )}
                  <p className="text-[10px] text-gray-600 mt-0.5">預設值：{it.default}</p>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2 mt-4">
              <button onClick={() => setParams(null)}
                className="py-2.5 bg-gray-700 text-gray-300 rounded-lg text-sm hover:bg-gray-600">
                取消
              </button>
              <button
                onClick={() => void saveParams()}
                disabled={busy === 'params'}
                className="py-2.5 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-500 disabled:opacity-50">
                {busy === 'params' ? '儲存中...' : '儲存參數'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <Toast msg={toast.msg} type={toast.type} />}
    </div>
  )
}
