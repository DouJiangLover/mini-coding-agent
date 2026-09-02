'use client';

import { useEffect, useMemo, useState } from 'react';

type AgentMode = 'safe' | 'standard' | 'autonomous' | 'read_only';

type AgentSettings = {
  mode: AgentMode;
  max_steps: number;
  failure_limit: number;
  interaction_first: boolean;
  require_verification: boolean;
  require_review: boolean;
  context_budget: number;
  command_timeout: number;
  updated_at: string;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000';

const MODE_OPTIONS: { id: AgentMode; title: string; eyebrow: string; description: string; detail: string }[] = [
  {
    id: 'standard',
    title: '标准模式',
    eyebrow: 'RECOMMENDED',
    description: '在自主性和控制力之间保持平衡。',
    detail: 'Skill 内的低风险操作自动执行，额外能力和高风险命令逐次确认。',
  },
  {
    id: 'safe',
    title: '安全模式',
    eyebrow: 'CONFIRM MORE',
    description: '在产生副作用前让用户充分参与。',
    detail: '创建文件、修改文件和运行命令都会暂停并请求单次授权。',
  },
  {
    id: 'autonomous',
    title: '自主模式',
    eyebrow: 'FEWER PAUSES',
    description: '减少低风险工具的确认中断。',
    detail: '开放全部本地工具，但危险命令、凭据和工作区边界仍不能绕过。',
  },
  {
    id: 'read_only',
    title: '只读审查',
    eyebrow: 'NO FILE CHANGES',
    description: '用于分析、解释和代码审查。',
    detail: '允许读取与检查，创建和修改文件即使获得单次授权也不会执行。',
  },
];

const DEFAULT_SETTINGS: AgentSettings = {
  mode: 'standard',
  max_steps: 45,
  failure_limit: 3,
  interaction_first: true,
  require_verification: true,
  require_review: true,
  context_budget: 48_000,
  command_timeout: 30,
  updated_at: '',
};

function settingsPayload(settings: AgentSettings) {
  return {
    mode: settings.mode,
    max_steps: settings.max_steps,
    failure_limit: settings.failure_limit,
    interaction_first: settings.interaction_first,
    require_verification: settings.require_verification,
    require_review: settings.require_review,
    context_budget: settings.context_budget,
    command_timeout: settings.command_timeout,
  };
}

async function responseError(response: Response) {
  try {
    const data = await response.json() as { detail?: string };
    return data.detail ?? '设置保存失败。';
  } catch {
    return '设置保存失败，请确认后端服务正常运行。';
  }
}

function SettingsSwitch({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) {
  return (
    <button className={`settings-switch ${checked ? 'on' : ''}`} type="button" role="switch" aria-checked={checked} aria-label={label} onClick={onChange}>
      <span />
    </button>
  );
}

export default function AgentSettingsPage() {
  const [settings, setSettings] = useState<AgentSettings>(DEFAULT_SETTINGS);
  const [savedSettings, setSavedSettings] = useState<AgentSettings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/settings`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response));
        return await response.json() as { settings: AgentSettings };
      })
      .then((data) => {
        if (cancelled) return;
        setSettings(data.settings);
        setSavedSettings(data.settings);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : '无法加载 Agent 设置');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const isDirty = useMemo(() => {
    return JSON.stringify(settingsPayload(settings)) !== JSON.stringify(settingsPayload(savedSettings));
  }, [savedSettings, settings]);

  function update<K extends keyof AgentSettings>(key: K, value: AgentSettings[K]) {
    setSettings((current) => ({ ...current, [key]: value }));
    setError('');
    setNotice('');
  }

  async function saveSettings() {
    if (saving || !isDirty) return;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settingsPayload(settings)),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json() as { settings: AgentSettings };
      setSettings(data.settings);
      setSavedSettings(data.settings);
      setNotice('Agent 设置已保存，将从下一个新任务开始生效。');
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '无法保存 Agent 设置');
    } finally {
      setSaving(false);
    }
  }

  async function resetSettings() {
    if (saving) return;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/settings/reset`, { method: 'POST' });
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json() as { settings: AgentSettings };
      setSettings(data.settings);
      setSavedSettings(data.settings);
      setNotice('已恢复推荐的标准配置。');
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : '无法恢复默认设置');
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="skill-manager-shell settings-page-shell">
      <header className="skill-manager-topbar">
        <a className="skill-brand" href="/" aria-label="返回 IntentFlow 工作台"><span>›_</span><strong>IntentFlow</strong></a>
        <div className="manager-topbar-links">
          <a className="back-to-workspace" href="/skills"><span>S</span> Skill 管理</a>
          <a className="back-to-workspace" href="/"><span>←</span> 返回工作台</a>
        </div>
      </header>

      <div className="settings-page-content">
        <section className="settings-heading">
          <div><span className="page-kicker">AGENT CONFIGURATION</span><h1>Agent 设置</h1><p>控制 IntentFlow 如何规划、行动、验证和停止。每个任务启动时会锁定一份配置快照，运行中的任务不会被中途改变。</p></div>
          <div className="settings-heading-actions">
            <button className="reset-settings-button" type="button" onClick={() => void resetSettings()} disabled={saving || loading}>恢复默认</button>
            <button className="save-settings-button" type="button" onClick={() => void saveSettings()} disabled={saving || loading || !isDirty}>{saving ? '保存中…' : '保存设置'}</button>
          </div>
        </section>

        {(error || notice) && <div className={`skill-feedback ${error ? 'error' : 'success'}`} role="status"><span>{error ? '!' : '✓'}</span>{error || notice}</div>}

        {loading ? (
          <div className="settings-loading"><span /><span /><span /><p>正在读取 Agent 配置…</p></div>
        ) : (
          <>
            <section className="settings-section">
              <header><div><span className="settings-section-index">01</span><h2>运行模式</h2></div><p>选择 Agent 的默认自主程度。硬安全边界在所有模式中保持不变。</p></header>
              <div className="agent-mode-grid">
                {MODE_OPTIONS.map((mode) => (
                  <button className={`agent-mode-card ${settings.mode === mode.id ? 'selected' : ''}`} type="button" onClick={() => update('mode', mode.id)} key={mode.id}>
                    <span className="mode-radio"><i /></span>
                    <small>{mode.eyebrow}</small>
                    <strong>{mode.title}</strong>
                    <p>{mode.description}</p>
                    <em>{mode.detail}</em>
                  </button>
                ))}
              </div>
            </section>

            <section className="settings-section">
              <header><div><span className="settings-section-index">02</span><h2>工作流关卡</h2></div><p>决定宿主程序必须收集哪些证据，模型提示词不能自行跳过已启用的关卡。</p></header>
              <div className="workflow-setting-list">
                <div>
                  <span className="setting-icon">F</span>
                  <div><strong>Interaction-First</strong><p>由独立需求分析器判断新建产品任务，先确认终端用户流程，再组合 Skill 并写代码。</p></div>
                  <SettingsSwitch checked={settings.interaction_first} onChange={() => update('interaction_first', !settings.interaction_first)} label="切换 Interaction-First" />
                </div>
                <div>
                  <span className="setting-icon">✓</span>
                  <div><strong>强制验证</strong><p>产生文件改动后，必须有成功的测试或检查结果才能完成任务。</p></div>
                  <SettingsSwitch checked={settings.require_verification} onChange={() => update('require_verification', !settings.require_verification)} label="切换强制验证" />
                </div>
                <div>
                  <span className="setting-icon">R</span>
                  <div><strong>完成前自检</strong><p>第一次提交完成后，要求重新读取改动或检查 Diff，再次确认结果。</p></div>
                  <SettingsSwitch checked={settings.require_review} onChange={() => update('require_review', !settings.require_review)} label="切换完成前自检" />
                </div>
              </div>
            </section>

            <section className="settings-section">
              <header><div><span className="settings-section-index">03</span><h2>执行预算</h2></div><p>更高预算能够处理更复杂的任务，但会增加耗时和模型调用量。</p></header>
              <div className="budget-setting-grid">
                <label className="range-setting-card">
                  <div><span>最大执行步骤</span><output>{settings.max_steps}</output></div>
                  <input type="range" min="5" max="100" step="5" value={settings.max_steps} onChange={(event) => update('max_steps', Number(event.target.value))} />
                  <small>保存后的设置优先生效；环境变量只作为首次运行默认值。复杂 Web 项目建议 60 步。</small>
                  <div className="range-labels"><span>5</span><span>100 步</span></div>
                </label>
                <label className="range-setting-card">
                  <div><span>连续失败上限</span><output>{settings.failure_limit}</output></div>
                  <input type="range" min="1" max="10" step="1" value={settings.failure_limit} onChange={(event) => update('failure_limit', Number(event.target.value))} />
                  <small>连续工具故障或无有效动作达到上限时停止。</small>
                  <div className="range-labels"><span>1</span><span>10 次</span></div>
                </label>
                <label className="range-setting-card">
                  <div><span>上下文预算</span><output>{Math.round(settings.context_budget / 1000)}K</output></div>
                  <input type="range" min="12000" max="200000" step="4000" value={settings.context_budget} onChange={(event) => update('context_budget', Number(event.target.value))} />
                  <small>超过预算后保留任务目标与最近工具反馈。</small>
                  <div className="range-labels"><span>12K</span><span>200K 字符</span></div>
                </label>
                <label className="range-setting-card">
                  <div><span>命令超时上限</span><output>{settings.command_timeout}s</output></div>
                  <input type="range" min="5" max="60" step="5" value={settings.command_timeout} onChange={(event) => update('command_timeout', Number(event.target.value))} />
                  <small>单次测试或检查超过此时间会被终止。</small>
                  <div className="range-labels"><span>5s</span><span>60s</span></div>
                </label>
              </div>
            </section>

            <aside className="immutable-safety-card">
              <span>◇</span>
              <div><strong>始终生效的安全边界</strong><p>工作区隔离、凭据文件保护、Shell 禁止、高风险删除参数拦截和 API Key 环境变量规则不能通过本页面关闭。自主模式只减少低风险操作的停顿，不等于无限权限。</p></div>
              <span className="immutable-badge">LOCKED</span>
            </aside>

            {isDirty && (
              <div className="settings-save-dock visible">
                <div><strong>有尚未保存的更改</strong><small>保存后从下一个新任务开始生效</small></div>
                <button type="button" onClick={() => setSettings(savedSettings)} disabled={saving}>放弃更改</button>
                <button type="button" onClick={() => void saveSettings()} disabled={saving}>{saving ? '保存中…' : '保存设置'}</button>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
