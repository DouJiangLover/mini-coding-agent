'use client';

import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';

type RunEvent = {
  event_id: number;
  run_id: string;
  type: string;
  phase: string;
  status: 'pending' | 'running' | 'success' | 'failed' | 'info';
  title: string;
  summary: string;
  timestamp: string;
  payload?: Record<string, unknown>;
};

type PlanItem = {
  id: string;
  title: string;
  status: 'pending' | 'running' | 'success' | 'failed';
};

type RunStatus = 'idle' | 'running' | 'completed' | 'failed' | 'cancelled';

type RunSnapshot = {
  run_id: string;
  task: string;
  workspace: string;
  status: string;
  events?: RunEvent[];
};

type StoredRun = {
  runId: string;
  task: string;
  workspace: string;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000';
const RUN_SESSION_KEY = 'tracecoder:last-run';

const PHASES = [
  { id: 'selecting_skill', label: '选择 Skill' },
  { id: 'planning', label: '制定计划' },
  { id: 'executing', label: '执行任务' },
  { id: 'verifying', label: '验证结果' },
  { id: 'completed', label: '完成' },
];

const IDLE_PLAN: PlanItem[] = [
  { id: 'inspect', title: '理解项目结构与任务约束', status: 'pending' },
  { id: 'diagnose', title: '定位失败原因', status: 'pending' },
  { id: 'edit', title: '实施最小范围修改', status: 'pending' },
  { id: 'verify', title: '运行测试并验证结果', status: 'pending' },
];

const PROJECT_WORKSPACES = [
  { icon: '∑', name: 'Calculator', path: 'examples/calculator', stack: 'Python · pytest' },
  { icon: '★', name: 'Star Catcher', path: 'examples/star-catcher', stack: 'HTML · CSS · JavaScript' },
  { icon: '20', name: '2048 Game', path: 'examples/2048-game', stack: '需求文档 · 从零构建' },
];

const EVENT_ICONS: Record<string, string> = {
  run_started: '↗',
  skill_selected: 'S',
  plan_updated: '≡',
  phase_changed: '◇',
  tool_started: '›_',
  tool_finished: '✓',
  file_changed: '±',
  error: '!',
  run_finished: '✓',
};

function formatTime(value: string) {
  if (!value) return '--:--';
  return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function payloadText(event?: RunEvent) {
  if (!event?.payload) return '选择一条执行记录查看详细信息。';
  const preferred = event.payload.output ?? event.payload.diff ?? event.payload.detail;
  if (typeof preferred === 'string') return preferred;
  return JSON.stringify(event.payload, null, 2);
}

function statusCopy(status: string) {
  if (status === 'running') return '正在工作';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '运行失败';
  if (status === 'cancelled') return '已停止';
  return '准备就绪';
}

function normalizeRunStatus(status: string): RunStatus {
  if (status === 'completed' || status === 'failed' || status === 'cancelled') return status;
  return 'running';
}

export default function Home() {
  const [task, setTask] = useState('');
  const [submittedTask, setSubmittedTask] = useState('');
  const [workspace, setWorkspace] = useState('examples/calculator');
  const [runId, setRunId] = useState<string>();
  const [runStatus, setRunStatus] = useState<RunStatus>('idle');
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [selectedId, setSelectedId] = useState<number>();
  const [plan, setPlan] = useState<PlanItem[]>(IDLE_PLAN);
  const [tab, setTab] = useState<'detail' | 'diff' | 'console'>('detail');
  const [notice, setNotice] = useState('');
  const [backendMode, setBackendMode] = useState<'demo' | 'model' | 'offline'>('offline');
  const [modelInfo, setModelInfo] = useState({ provider: '', model: '' });
  const streamRef = useRef<EventSource | null>(null);
  const conversationRef = useRef<HTMLDivElement | null>(null);
  const activeWorkspace = PROJECT_WORKSPACES.find((item) => item.path === workspace) ?? PROJECT_WORKSPACES[0];

  const attachStream = useCallback((id: string) => {
    streamRef.current?.close();
    const stream = new EventSource(`${API_BASE}/api/runs/${id}/events`);
    streamRef.current = stream;
    stream.onmessage = (message) => {
      const event = JSON.parse(message.data) as RunEvent;
      setEvents((current) => current.some((item) => item.event_id === event.event_id) ? current : [...current, event]);
      setSelectedId(event.event_id);
      setNotice('');

      if (event.type === 'plan_updated' && Array.isArray(event.payload?.items)) {
        setPlan(event.payload.items as PlanItem[]);
      }
      if (event.type === 'run_finished') {
        setRunStatus(normalizeRunStatus(String(event.payload?.status ?? 'completed')));
        stream.close();
      }
    };
    stream.onerror = () => {
      if (stream.readyState !== EventSource.CLOSED) setNotice('实时连接暂时中断，正在自动重连…');
    };
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then(async (response) => {
        if (!response.ok) throw new Error('offline');
        return await response.json() as { mode?: string; provider?: string; model?: string };
      })
      .then((data) => {
        setBackendMode(data.mode === 'model' ? 'model' : 'demo');
        setModelInfo({ provider: data.provider ?? '', model: data.model ?? '' });
      })
      .catch(() => setBackendMode('offline'));
    return () => streamRef.current?.close();
  }, []);

  useEffect(() => {
    const storedText = sessionStorage.getItem(RUN_SESSION_KEY);
    if (!storedText) return;

    let stored: StoredRun;
    try {
      stored = JSON.parse(storedText) as StoredRun;
      if (!stored.runId || !stored.task || !stored.workspace) throw new Error('invalid run session');
    } catch {
      sessionStorage.removeItem(RUN_SESSION_KEY);
      return;
    }

    let disposed = false;
    fetch(`${API_BASE}/api/runs/${stored.runId}`)
      .then(async (response) => {
        if (!response.ok) {
          if (response.status === 404) sessionStorage.removeItem(RUN_SESSION_KEY);
          throw new Error(await response.text());
        }
        return await response.json() as RunSnapshot;
      })
      .then((snapshot) => {
        if (disposed) return;
        const restoredEvents = Array.isArray(snapshot.events) ? snapshot.events : [];
        const restoredPlan = [...restoredEvents].reverse().find(
          (event) => event.type === 'plan_updated' && Array.isArray(event.payload?.items),
        );
        const restoredStatus = normalizeRunStatus(snapshot.status);

        setRunId(snapshot.run_id);
        setSubmittedTask(snapshot.task);
        setWorkspace(snapshot.workspace);
        setEvents(restoredEvents);
        setSelectedId(restoredEvents.at(-1)?.event_id);
        if (restoredPlan) setPlan(restoredPlan.payload?.items as PlanItem[]);
        setRunStatus(restoredStatus);
        if (restoredStatus === 'running') attachStream(snapshot.run_id);
      })
      .catch(() => {
        if (disposed) return;
        setRunStatus('failed');
        setNotice('上次任务状态暂时无法恢复，请确认后端服务仍在运行。');
      });

    return () => {
      disposed = true;
    };
  }, [attachStream]);

  useEffect(() => {
    if (!events.length) return;
    conversationRef.current?.scrollTo({ top: conversationRef.current.scrollHeight, behavior: 'smooth' });
  }, [events.length]);

  const activeEvent = events.find((event) => event.event_id === selectedId) ?? events.at(-1);
  const currentPhase = events.at(-1)?.phase ?? 'created';
  const currentPhaseIndex = PHASES.findIndex((phase) => phase.id === currentPhase);
  const selectedSkill = [...events].reverse().find((event) => event.type === 'skill_selected');
  const finalEvent = [...events].reverse().find((event) => event.type === 'run_finished');
  const completedSteps = plan.filter((item) => item.status === 'success').length;
  const changedFiles = [...new Set(events.filter((event) => event.type === 'file_changed').map((event) => String(event.payload?.path ?? '')))].filter(Boolean);
  const activityEvents = events.filter((event) => ['skill_selected', 'phase_changed', 'tool_finished', 'file_changed', 'error'].includes(event.type));
  const chatEvents = events.filter((event) => ['skill_selected', 'tool_finished', 'file_changed', 'error'].includes(event.type));

  const tabContent = useMemo(() => {
    if (tab === 'diff') {
      const event = [...events].reverse().find((item) => item.type === 'file_changed');
      return typeof event?.payload?.diff === 'string' ? event.payload.diff : '本次任务还没有文件改动。';
    }
    if (tab === 'console') {
      const outputs = events
        .filter((item) => typeof item.payload?.output === 'string')
        .map((item) => `$ ${String(item.payload?.command ?? item.title)}\n${item.payload?.output}`);
      return outputs.join('\n\n') || '命令输出会显示在这里。';
    }
    return payloadText(activeEvent);
  }, [activeEvent, events, tab]);

  async function startRun() {
    const submitted = task.trim();
    if (!submitted || runStatus === 'running') return;
    setNotice('');
    setSubmittedTask(submitted);
    setEvents([]);
    setPlan(IDLE_PLAN);
    setSelectedId(undefined);
    setRunStatus('running');
    try {
      const response = await fetch(`${API_BASE}/api/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: submitted, workspace: workspace.trim() || '.' }),
      });
      if (!response.ok) throw new Error(await response.text());
      const data = await response.json() as { run_id: string };
      sessionStorage.setItem(RUN_SESSION_KEY, JSON.stringify({
        runId: data.run_id,
        task: submitted,
        workspace: workspace.trim() || '.',
      } satisfies StoredRun));
      setTask('');
      setRunId(data.run_id);
      attachStream(data.run_id);
    } catch (error) {
      setRunStatus('failed');
      setNotice(error instanceof Error ? error.message : '无法启动任务');
    }
  }

  async function cancelRun() {
    if (!runId) return;
    await fetch(`${API_BASE}/api/runs/${runId}/cancel`, { method: 'POST' });
  }

  async function resetDemo() {
    try {
      const response = await fetch(`${API_BASE}/api/demo/reset?workspace=${encodeURIComponent(workspace)}`, { method: 'POST' });
      if (!response.ok) throw new Error(await response.text());
      setNotice(`${activeWorkspace.name} 工作区已恢复，可以重新运行任务。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '重置失败');
    }
  }

  function newRun() {
    if (runStatus === 'running') return;
    sessionStorage.removeItem(RUN_SESSION_KEY);
    setTask('');
    setSubmittedTask('');
    setRunId(undefined);
    setRunStatus('idle');
    setEvents([]);
    setPlan(IDLE_PLAN);
    setSelectedId(undefined);
    setNotice('');
  }

  function selectWorkspace(nextWorkspace: string) {
    if (runStatus === 'running' || nextWorkspace === workspace) return;
    streamRef.current?.close();
    sessionStorage.removeItem(RUN_SESSION_KEY);
    setWorkspace(nextWorkspace);
    setTask('');
    setSubmittedTask('');
    setRunId(undefined);
    setRunStatus('idle');
    setEvents([]);
    setPlan(IDLE_PLAN);
    setSelectedId(undefined);
    setNotice('');
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void startRun();
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="brand-lockup">
            <span className="brand-glyph" aria-hidden="true">›_</span>
            <strong>TraceCoder</strong>
          </div>
          <button className="new-run-button" type="button" onClick={newRun} disabled={runStatus === 'running'}>
            <span aria-hidden="true">＋</span> 新建任务
          </button>
        </div>

        <nav className="sidebar-nav" aria-label="任务导航">
          <p>工作区</p>
          {PROJECT_WORKSPACES.map((project) => (
            <button
              className={`nav-item ${workspace === project.path ? 'active' : ''}`}
              type="button"
              key={project.path}
              onClick={() => selectWorkspace(project.path)}
              disabled={runStatus === 'running'}
              aria-pressed={workspace === project.path}
            >
              <span className="nav-icon">{project.icon}</span>
              <span><strong>{project.name}</strong><small>{project.stack}</small></span>
            </button>
          ))}

          <p className="sidebar-section-label">演示控制</p>
          <button className="nav-item" type="button" onClick={resetDemo} disabled={runStatus === 'running'}>
            <span className="nav-icon">↻</span>
            <span>
              <strong>重置演示项目</strong>
              <small>恢复 {activeWorkspace.name} 的初始故障</small>
            </span>
          </button>

        </nav>

        <div className="sidebar-footer">
          <div className="connection-line">
            <span className={`connection-dot ${backendMode}`} />
            <span>
              {backendMode === 'model'
                ? `${modelInfo.provider === 'deepseek' ? 'DeepSeek' : '模型 API'} · ${modelInfo.model}`
                : backendMode === 'demo' ? '本地演示模式' : '后端未连接'}
            </span>
          </div>
          <div className="profile-row">
            <span className="profile-avatar">L</span>
            <span><strong>Local workspace</strong><small>受控文件与命令</small></span>
            <span className="more">•••</span>
          </div>
        </div>
      </aside>

      <section className="conversation-shell">
        <header className="conversation-header">
          <div className="workspace-breadcrumb">
            <span className="folder-mark">⌁</span>
            <span>coding agent</span>
            <span className="slash">/</span>
            <strong>{workspace || '.'}</strong>
          </div>
          <div className="header-actions">
            {runId && <span className="run-id">{runId.slice(0, 12)}</span>}
            <span className={`run-state ${runStatus}`}><i />{statusCopy(runStatus)}</span>
          </div>
        </header>

        <div className="conversation-scroll" ref={conversationRef}>
          {submittedTask && (
            <div className="chat-thread">
              <section className="message user-message">
                <div className="message-avatar user-avatar">你</div>
                <div className="message-body">
                  <div className="message-meta"><strong>你</strong><time>刚刚</time></div>
                  <p>{submittedTask}</p>
                </div>
              </section>

              <section className="message agent-message">
                <div className="message-avatar agent-avatar">›_</div>
                <div className="message-body">
                  <div className="message-meta"><strong>TraceCoder</strong><span className="agent-badge">AGENT</span></div>
                  <p className="agent-intro">
                    {selectedSkill
                      ? `已加载 ${selectedSkill.title}。我会先理解项目并复现问题，然后实施最小修改并运行验证。`
                      : '正在分析任务并选择合适的 Skill…'}
                  </p>

                  {chatEvents.length > 0 && (
                    <div className="inline-activity">
                      <div className="inline-activity-head">
                        <span>执行过程</span>
                        <small>{activityEvents.length} 条记录</small>
                      </div>
                      {chatEvents.slice(-8).map((event) => (
                        <button type="button" key={event.event_id} onClick={() => setSelectedId(event.event_id)}>
                          <span className={`mini-event-icon ${event.status}`}>{EVENT_ICONS[event.type] ?? '·'}</span>
                          <span><strong>{event.title}</strong><small>{event.summary}</small></span>
                          <time>{formatTime(event.timestamp)}</time>
                        </button>
                      ))}
                    </div>
                  )}

                  {runStatus === 'running' && (
                    <div className="thinking-row"><span /><span /><span /><small>正在根据工具结果继续工作</small></div>
                  )}

                  {finalEvent && (
                    <div className={`final-answer ${runStatus}`}>
                      <div className="final-answer-icon">{runStatus === 'completed' ? '✓' : '!'}</div>
                      <div>
                        <strong>{runStatus === 'completed' ? '任务已完成' : '任务未完成'}</strong>
                        <p>{finalEvent.summary}</p>
                        <div className="result-chips">
                          {changedFiles.map((file) => <span key={file}>± {file}</span>)}
                          {runStatus === 'completed' && <span>✓ 验证通过</span>}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </section>
            </div>
          )}
        </div>

        <div className="composer-zone">
          {notice && <div className="notice" role="status"><span>i</span>{notice}</div>}
          <div className="composer-card">
            <textarea
              value={task}
              onChange={(event) => setTask(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              rows={3}
              aria-label="给 TraceCoder 的任务"
              placeholder={`描述要在 ${activeWorkspace.name} 工作区完成的任务…`}
            />
            <div className="composer-toolbar">
              <div className="path-pill" title={workspace}>
                <span>⌁</span>
                <code>{workspace}</code>
              </div>
              <span className="keyboard-hint">Enter 发送 · Shift + Enter 换行</span>
              {runStatus === 'running' ? (
                <button className="stop-button" type="button" onClick={cancelRun} aria-label="停止任务">■</button>
              ) : (
                <button className="send-button" type="button" onClick={startRun} disabled={!task.trim()} aria-label="开始运行">↑</button>
              )}
            </div>
          </div>
          <p className="composer-note">TraceCoder 会在本地受控工作区中读写文件并执行命令，请审查重要改动。</p>
        </div>
      </section>

      <aside className="inspector">
        <header className="inspector-header">
          <div><span className="panel-kicker">RUN DETAILS</span><h2>Agent 运行</h2></div>
          <span className={`live-indicator ${runStatus}`}><i />{runStatus === 'running' ? 'LIVE' : runStatus === 'completed' ? 'DONE' : 'IDLE'}</span>
        </header>

        <div className="inspector-scroll">
          <section className="inspector-section">
            <div className="section-title"><span>计划</span><small>{completedSteps}/{plan.length}</small></div>
            <div className="progress-track"><span style={{ width: `${(completedSteps / plan.length) * 100}%` }} /></div>
            <div className="compact-plan">
              {plan.map((item) => (
                <div className={item.status} key={item.id}>
                  <span>{item.status === 'success' ? '✓' : item.status === 'running' ? '●' : ''}</span>
                  <p>{item.title}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="inspector-section phase-strip-section">
            <div className="section-title"><span>阶段</span><small>{Math.max(0, currentPhaseIndex + 1)}/{PHASES.length}</small></div>
            <div className="phase-strip">
              {PHASES.map((phase, index) => (
                <div key={phase.id} className={`${phase.id === currentPhase || (currentPhase === 'recovering' && phase.id === 'executing') ? 'active' : ''} ${currentPhase === 'completed' || currentPhaseIndex > index ? 'done' : ''}`}>
                  <span>{currentPhase === 'completed' || currentPhaseIndex > index ? '✓' : index + 1}</span>
                  <small>{phase.label}</small>
                </div>
              ))}
            </div>
          </section>

          {selectedSkill && (
            <section className="skill-banner">
              <span className="skill-symbol">S</span>
              <div><small>ACTIVE SKILL</small><strong>{selectedSkill.title}</strong><p>{selectedSkill.summary}</p></div>
            </section>
          )}

          <section className="inspector-section activity-section">
            <div className="section-title"><span>活动</span><small>{activityEvents.length}</small></div>
            <div className="activity-list">
              {activityEvents.length === 0 ? (
                <div className="activity-empty"><span>◇</span><p>开始任务后，工具调用会显示在这里。</p></div>
              ) : activityEvents.map((event) => (
                <button
                  type="button"
                  key={event.event_id}
                  className={`${event.status} ${event.event_id === activeEvent?.event_id ? 'selected' : ''}`}
                  onClick={() => setSelectedId(event.event_id)}
                >
                  <span className="activity-icon">{EVENT_ICONS[event.type] ?? '·'}</span>
                  <span className="activity-copy"><strong>{event.title}</strong><small>{event.summary}</small></span>
                  <time>{formatTime(event.timestamp)}</time>
                </button>
              ))}
            </div>
          </section>

          <section className="detail-drawer">
            <div className="detail-tabs" role="tablist">
              <button className={tab === 'detail' ? 'active' : ''} onClick={() => setTab('detail')} role="tab">详情</button>
              <button className={tab === 'diff' ? 'active' : ''} onClick={() => setTab('diff')} role="tab">Diff {changedFiles.length > 0 && <span>{changedFiles.length}</span>}</button>
              <button className={tab === 'console' ? 'active' : ''} onClick={() => setTab('console')} role="tab">终端</button>
            </div>
            <div className="selected-event-head">
              <span className={`selected-event-icon ${activeEvent?.status ?? 'pending'}`}>{EVENT_ICONS[activeEvent?.type ?? ''] ?? '·'}</span>
              <div><strong>{activeEvent?.title ?? '暂无执行记录'}</strong><small>{activeEvent?.summary ?? '选择一条活动查看完整信息。'}</small></div>
            </div>
            <pre className={`code-view ${tab}`}>{tabContent}</pre>
          </section>
        </div>

        <footer className="guard-footer"><span>◇</span><p><strong>Workspace Guard</strong><small>路径与命令均在本地校验</small></p></footer>
      </aside>
    </main>
  );
}
