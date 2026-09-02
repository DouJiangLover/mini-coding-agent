'use client';

import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';

type SkillItem = {
  name: string;
  display_name: string;
  description: string;
  keywords: string[];
  allowed_tools: string[];
  prompt: string;
  source: 'built_in' | 'custom';
  created_at: string;
  enabled: boolean;
};

type SkillListResponse = {
  skills: SkillItem[];
  total: number;
  enabled: number;
  available_tools: string[];
};

type SkillForm = {
  displayName: string;
  description: string;
  keywords: string;
  prompt: string;
  allowedTools: string[];
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000';

const TOOL_COPY: Record<string, { label: string; description: string }> = {
  list_files: { label: '查看目录', description: '了解项目结构' },
  read_file: { label: '读取文件', description: '读取工作区文本' },
  search_text: { label: '搜索代码', description: '按关键词定位实现' },
  create_file: { label: '创建文件', description: '创建新的项目文件' },
  apply_patch: { label: '修改文件', description: '应用局部代码补丁' },
  run_command: { label: '运行命令', description: '运行受控测试和检查' },
  finish: { label: '提交结果', description: '结束任务并总结' },
};

const EMPTY_FORM: SkillForm = {
  displayName: '',
  description: '',
  keywords: '',
  prompt: '',
  allowedTools: ['list_files', 'read_file', 'search_text', 'apply_patch', 'run_command', 'finish'],
};

async function responseError(response: Response) {
  try {
    const body = await response.json() as { detail?: string };
    return body.detail ?? '操作失败，请稍后重试。';
  } catch {
    return '操作失败，请确认后端服务正常运行。';
  }
}

export default function SkillManagerPage() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [availableTools, setAvailableTools] = useState<string[]>(Object.keys(TOOL_COPY));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busySkill, setBusySkill] = useState('');
  const [filter, setFilter] = useState<'all' | 'enabled' | 'custom'>('all');
  const [query, setQuery] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [form, setForm] = useState<SkillForm>(EMPTY_FORM);
  const skillFileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/skills`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response));
        return await response.json() as SkillListResponse;
      })
      .then((data) => {
        if (cancelled) return;
        setSkills(data.skills);
        setAvailableTools(data.available_tools);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : '无法加载 Skill');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const enabledCount = skills.filter((skill) => skill.enabled).length;
  const customCount = skills.filter((skill) => skill.source === 'custom').length;
  const visibleSkills = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return skills.filter((skill) => {
      if (filter === 'enabled' && !skill.enabled) return false;
      if (filter === 'custom' && skill.source !== 'custom') return false;
      if (!normalizedQuery) return true;
      return [skill.display_name, skill.description, ...skill.keywords]
        .some((value) => value.toLocaleLowerCase().includes(normalizedQuery));
    });
  }, [filter, query, skills]);

  async function toggleSkill(skill: SkillItem) {
    if (busySkill) return;
    setBusySkill(skill.name);
    setError('');
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(skill.name)}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !skill.enabled }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json() as { skill: SkillItem };
      setSkills((current) => current.map((item) => item.name === data.skill.name ? data.skill : item));
      setNotice(`${skill.display_name} 已${data.skill.enabled ? '启用' : '停用'}，将在下一个任务中生效。`);
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : '无法更新 Skill');
    } finally {
      setBusySkill('');
    }
  }

  function toggleTool(tool: string) {
    if (tool === 'finish') return;
    setForm((current) => ({
      ...current,
      allowedTools: current.allowedTools.includes(tool)
        ? current.allowedTools.filter((item) => item !== tool)
        : [...current.allowedTools, tool],
    }));
  }

  async function createSkill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creating) return;
    const keywords = form.keywords.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
    setCreating(true);
    setError('');
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/skills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          display_name: form.displayName.trim(),
          description: form.description.trim(),
          keywords,
          allowed_tools: form.allowedTools,
          prompt: form.prompt.trim(),
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json() as { skill: SkillItem };
      setSkills((current) => [...current, data.skill]);
      setForm(EMPTY_FORM);
      setShowCreate(false);
      setFilter('all');
      setNotice(`${data.skill.display_name} 已创建并启用，可以立即参与新任务路由。`);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : '无法创建 Skill');
    } finally {
      setCreating(false);
    }
  }

  async function importSkillFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || importing) return;
    setError('');
    setNotice('');
    if (file.size > 1_000_000) {
      setError('Skill 文件不能超过 1 MB。');
      event.target.value = '';
      return;
    }

    setImporting(true);
    try {
      const response = await fetch(`${API_BASE}/api/skills/import?filename=${encodeURIComponent(file.name)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const data = await response.json() as { skill: SkillItem; format: string };
      setSkills((current) => [...current, data.skill]);
      setFilter('custom');
      setQuery('');
      setNotice(`${data.skill.display_name} 已从 ${data.format} 导入并启用，可以参与下一个任务的 Skill 路由。`);
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '无法导入 Skill 文件');
    } finally {
      setImporting(false);
      event.target.value = '';
    }
  }

  return (
    <main className="skill-manager-shell">
      <header className="skill-manager-topbar">
        <a className="skill-brand" href="/" aria-label="返回 IntentFlow 工作台">
          <span>›_</span><strong>IntentFlow</strong>
        </a>
        <div className="manager-topbar-links">
          <a className="back-to-workspace" href="/settings"><span>⚙</span> Agent 设置</a>
          <a className="back-to-workspace" href="/"><span>←</span> 返回工作台</a>
        </div>
      </header>

      <div className="skill-manager-content">
        <section className="skill-manager-heading">
          <div>
            <span className="page-kicker">AGENT CAPABILITIES</span>
            <h1>Skill 管理</h1>
            <p>决定 Agent 可以组合哪些工作策略。自动模式会按任务选择最多三项互补 Skill；交互流程由独立需求分析器控制。</p>
          </div>
          <div className="skill-manager-heading-actions">
            <div>
              <input
                ref={skillFileInputRef}
                className="skill-file-input"
                type="file"
                accept=".zip,.json,.md,.markdown,application/zip,application/json,text/markdown"
                onChange={(event) => void importSkillFile(event)}
                disabled={importing}
                aria-label="选择要导入的 Skill 文件"
              />
              <button className="import-skill-button" type="button" onClick={() => skillFileInputRef.current?.click()} disabled={importing}>
                <span>⇧</span> {importing ? '正在导入…' : '导入 Skill 文件'}
              </button>
              <button className="create-skill-button" type="button" onClick={() => { setShowCreate(true); setError(''); }}>
                <span>＋</span> 添加 Skill
              </button>
            </div>
            <small>支持 ZIP、JSON 和 SKILL.md，最大 1 MB</small>
          </div>
        </section>

        <section className="skill-stat-grid" aria-label="Skill 统计">
          <div><span>可用能力</span><strong>{enabledCount}</strong><small>新任务可参与匹配</small></div>
          <div><span>全部 Skill</span><strong>{skills.length}</strong><small>内置与自定义能力</small></div>
          <div><span>自定义</span><strong>{customCount}</strong><small>保存在本地运行目录</small></div>
        </section>

        {(error || notice) && (
          <div className={`skill-feedback ${error ? 'error' : 'success'}`} role="status">
            <span>{error ? '!' : '✓'}</span>{error || notice}
          </div>
        )}

        <section className="skill-library">
          <div className="skill-library-toolbar">
            <div className="skill-filter-tabs" role="tablist" aria-label="筛选 Skill">
              <button type="button" className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>全部</button>
              <button type="button" className={filter === 'enabled' ? 'active' : ''} onClick={() => setFilter('enabled')}>已启用</button>
              <button type="button" className={filter === 'custom' ? 'active' : ''} onClick={() => setFilter('custom')}>自定义</button>
            </div>
            <label className="skill-search">
              <span>⌕</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称、说明或触发词" aria-label="搜索 Skill" />
            </label>
          </div>

          {loading ? (
            <div className="skill-loading"><span /><span /><span /><p>正在加载 Skill…</p></div>
          ) : visibleSkills.length === 0 ? (
            <div className="skill-empty"><span>S</span><strong>没有匹配的 Skill</strong><p>调整筛选条件，或创建一个新的能力。</p></div>
          ) : (
            <div className="skill-card-grid">
              {visibleSkills.map((skill, index) => (
                <article className={`skill-card ${skill.enabled ? 'enabled' : 'disabled'}`} key={skill.name}>
                  <div className="skill-card-top">
                    <span className="skill-card-icon">{skill.source === 'custom' ? 'C' : index + 1}</span>
                    <div>
                      <div className="skill-name-line">
                        <h2>{skill.display_name}</h2>
                        <span className={`skill-source ${skill.source}`}>{skill.source === 'custom' ? '自定义' : '内置'}</span>
                      </div>
                      <p>{skill.description}</p>
                    </div>
                    <button
                      className={`skill-switch ${skill.enabled ? 'on' : ''}`}
                      type="button"
                      role="switch"
                      aria-checked={skill.enabled}
                      aria-label={`${skill.enabled ? '停用' : '启用'} ${skill.display_name}`}
                      onClick={() => void toggleSkill(skill)}
                      disabled={busySkill === skill.name}
                    >
                      <span />
                    </button>
                  </div>

                  <div className="skill-card-section">
                    <small>触发词</small>
                    <div className="skill-tags">{skill.keywords.map((keyword) => <span key={keyword}>{keyword}</span>)}</div>
                  </div>
                  <div className="skill-card-section">
                    <small>默认工具权限</small>
                    <div className="skill-tool-list">
                      {skill.allowed_tools.map((tool) => <span key={tool}>{TOOL_COPY[tool]?.label ?? tool}</span>)}
                    </div>
                  </div>
                  <div className="skill-card-footer">
                    <span><i className={skill.enabled ? 'active' : ''} />{skill.enabled ? '已启用' : '已停用'}</span>
                    <code>{skill.name}</code>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <aside className="skill-safety-note">
          <span>◇</span>
          <div><strong>安全边界不会被 Skill 改写</strong><p>自定义 Skill 只影响任务策略、触发词和默认工具集合。工作区隔离、凭据保护、高风险拦截和单次授权仍由宿主程序统一执行。</p></div>
        </aside>
      </div>

      {showCreate && (
        <div className="skill-modal-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !creating) setShowCreate(false);
        }}>
          <section className="skill-create-panel" role="dialog" aria-modal="true" aria-labelledby="create-skill-title">
            <header>
              <div><span className="page-kicker">NEW CAPABILITY</span><h2 id="create-skill-title">添加自定义 Skill</h2></div>
              <button type="button" onClick={() => setShowCreate(false)} disabled={creating} aria-label="关闭">×</button>
            </header>
            <form onSubmit={createSkill}>
              <div className="skill-form-grid">
                <label>
                  <span>Skill 名称</span>
                  <input required minLength={2} maxLength={60} value={form.displayName} onChange={(event) => setForm({ ...form, displayName: event.target.value })} placeholder="例如：性能分析 Skill" />
                </label>
                <label>
                  <span>触发词</span>
                  <input required value={form.keywords} onChange={(event) => setForm({ ...form, keywords: event.target.value })} placeholder="性能, benchmark, 慢查询" />
                  <small>使用逗号分隔，任务命中时会提高该 Skill 的优先级。</small>
                </label>
              </div>
              <label>
                <span>适用场景</span>
                <textarea required minLength={4} maxLength={500} rows={2} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder="说明这个 Skill 擅长解决什么问题。" />
              </label>
              <label>
                <span>执行策略</span>
                <textarea required minLength={10} maxLength={4000} rows={5} value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} placeholder="例如：先运行基准测试建立性能基线，再定位热点函数。修改后必须使用相同命令复测，不做无关重构。" />
                <small>这些指令会在 Skill 被选中时加入模型上下文，请勿填写 API Key、密码或其它凭据。</small>
              </label>

              <fieldset className="skill-tool-picker">
                <legend>默认工具权限</legend>
                <p>未勾选的工具仍可由 Agent 请求单次授权，不会被静默放行。</p>
                <div>
                  {availableTools.map((tool) => {
                    const checked = form.allowedTools.includes(tool);
                    const locked = tool === 'finish';
                    return (
                      <label className={`${checked ? 'selected' : ''} ${locked ? 'locked' : ''}`} key={tool}>
                        <input type="checkbox" checked={checked} onChange={() => toggleTool(tool)} disabled={locked} />
                        <span>{checked ? '✓' : ''}</span>
                        <div><strong>{TOOL_COPY[tool]?.label ?? tool}</strong><small>{TOOL_COPY[tool]?.description ?? tool}</small></div>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              <footer>
                <button className="cancel-skill-create" type="button" onClick={() => setShowCreate(false)} disabled={creating}>取消</button>
                <button className="save-skill-create" type="submit" disabled={creating}>{creating ? '正在创建…' : '创建并启用'}</button>
              </footer>
            </form>
          </section>
        </div>
      )}
    </main>
  );
}
