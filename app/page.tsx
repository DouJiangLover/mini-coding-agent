'use client';

import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';

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

type RunStatus = 'idle' | 'running' | 'waiting_skill_confirmation' | 'waiting_approval' | 'waiting_interaction_confirmation' | 'completed' | 'failed' | 'cancelled';

type SkillOption = {
  name: string;
  display_name: string;
  description: string;
  source: 'built_in' | 'custom';
  enabled: boolean;
};

type SkillCandidate = {
  name: string;
  display_name: string;
  description: string;
  source: 'built_in' | 'custom';
  keyword_score: number;
  matched_keywords: string[];
};

type InteractionModel = {
  model_id: string;
  revision: number;
  title: string;
  summary: string;
  pages: { id: string; name: string; purpose: string }[];
  flows: { from: string; action: string; to: string }[];
  states: { from: string; event: string; to: string }[];
  acceptance_criteria: string[];
};

type RunSnapshot = {
  run_id: string;
  task: string;
  workspace: string;
  requested_skill?: string;
  status: string;
  events?: RunEvent[];
};

type StoredRun = {
  runId: string;
  task: string;
  workspace: string;
};

type TaskSummary = {
  run_id: string;
  task: string;
  workspace: string;
  status: string;
  phase: string;
  summary: string;
  created_at: string;
  completed_steps: number;
  total_steps: number;
  changed_files: number;
  last_event?: {
    type?: string;
    title?: string;
    summary?: string;
    timestamp?: string;
  } | null;
};

type WorkspaceDirectory = {
  name: string;
  path: string;
};

type WorkspaceListing = {
  root_path: string;
  current: string;
  parent: string | null;
  directories: WorkspaceDirectory[];
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8000';
const RUN_SESSION_KEY = 'tracecoder:last-run';
const ACTIVE_TASK_STATUSES = ['created', 'running', 'waiting_skill_confirmation', 'waiting_approval', 'waiting_interaction_confirmation'];

const PHASES = [
  { id: 'selecting_skill', label: '选择 Skill' },
  { id: 'interaction_modeling', label: '交互建模' },
  { id: 'interaction_confirmation', label: '确认流程' },
  { id: 'planning', label: '制定计划' },
  { id: 'inspecting', label: '理解项目' },
  { id: 'reproducing', label: '建立基线' },
  { id: 'diagnosing', label: '定位根因' },
  { id: 'implementing', label: '实施修改' },
  { id: 'verifying', label: '运行验证' },
  { id: 'reviewing', label: '完成前自检' },
  { id: 'completed', label: '完成' },
];

const IDLE_PLAN: PlanItem[] = [
  { id: 'inspect', title: '理解项目结构与任务约束', status: 'pending' },
  { id: 'baseline', title: '运行现有检查建立基线', status: 'pending' },
  { id: 'diagnose', title: '定位失败原因', status: 'pending' },
  { id: 'edit', title: '实施最小范围修改', status: 'pending' },
  { id: 'verify', title: '运行测试并验证结果', status: 'pending' },
  { id: 'review', title: '完成前审查改动与遗漏', status: 'pending' },
];

const PROJECT_WORKSPACES = [
  { icon: '∑', name: 'Calculator', path: 'examples/calculator', stack: 'Python · pytest' },
  { icon: '★', name: 'Star Catcher', path: 'examples/star-catcher', stack: 'HTML · CSS · JavaScript' },
  { icon: '20', name: '2048 Game', path: 'examples/2048-game', stack: '需求文档 · 从零构建' },
  { icon: '✓', name: 'Approval Demo', path: 'examples/approval-demo', stack: 'Python · 单次授权演示' },
  { icon: '!', name: 'Failure Lab', path: 'examples/order-engine-lab', stack: 'Python · 复杂故障实验' },
];

const EVENT_ICONS: Record<string, string> = {
  run_started: '↗',
  skill_candidates: 'S',
  skill_confirmation_requested: '?',
  skill_confirmation_resolved: '✓',
  skill_selected: 'S',
  plan_updated: '≡',
  phase_changed: '◇',
  tool_started: '›_',
  tool_finished: '✓',
  file_changed: '±',
  approval_requested: '⌁',
  approval_resolved: '✓',
  interaction_context_collected: 'D',
  interaction_model_created: 'F',
  interaction_confirmation_requested: '?',
  interaction_confirmation_resolved: '✓',
  quality_checkpoint: 'Q',
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
  if (status === 'waiting_skill_confirmation') return '等待 Skill 确认';
  if (status === 'waiting_approval') return '等待授权';
  if (status === 'waiting_interaction_confirmation') return '等待流程确认';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '运行失败';
  if (status === 'cancelled') return '已停止';
  return '准备就绪';
}

function inspectorStatusCopy(status: RunStatus) {
  if (status === 'running') return 'LIVE';
  if (status === 'waiting_skill_confirmation') return 'SKILL REVIEW';
  if (status === 'waiting_approval') return 'REVIEW';
  if (status === 'waiting_interaction_confirmation') return 'MODEL REVIEW';
  if (status === 'completed') return 'DONE';
  if (status === 'failed') return 'FAILED';
  if (status === 'cancelled') return 'STOPPED';
  return 'IDLE';
}

function eventIcon(event?: RunEvent) {
  if (!event) return '·';
  if (event.status === 'failed') return '!';
  return EVENT_ICONS[event.type] ?? '·';
}

function normalizeRunStatus(status: string): RunStatus {
  if (status === 'waiting_skill_confirmation' || status === 'waiting_approval' || status === 'waiting_interaction_confirmation' || status === 'completed' || status === 'failed' || status === 'cancelled') return status;
  return 'running';
}

function isActiveStatus(status: RunStatus) {
  return status === 'running' || status === 'waiting_skill_confirmation' || status === 'waiting_approval' || status === 'waiting_interaction_confirmation';
}

function isTaskActive(status: string) {
  return ACTIVE_TASK_STATUSES.includes(status);
}

function workspacePathsOverlap(first: string, second: string) {
  const firstParts = first.split('/').filter((part) => part && part !== '.');
  const secondParts = second.split('/').filter((part) => part && part !== '.');
  const sharedLength = Math.min(firstParts.length, secondParts.length);
  return firstParts.slice(0, sharedLength).every((part, index) => part === secondParts[index]);
}

function taskStatusLabel(status: string) {
  if (status === 'waiting_skill_confirmation') return '等待选择 Skill';
  if (status === 'waiting_approval') return '等待授权';
  if (status === 'waiting_interaction_confirmation') return '等待流程确认';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'cancelled') return '已停止';
  return '运行中';
}

function needsAttention(status: string) {
  return ['waiting_skill_confirmation', 'waiting_approval', 'waiting_interaction_confirmation'].includes(status);
}

type DiagramNode = { id: string; title: string; subtitle?: string };
type DiagramEdge = { from: string; to: string; label: string };
type PositionedNode = DiagramNode & { x: number; y: number; depth: number };

function createDiagramLayout(nodes: DiagramNode[], edges: DiagramEdge[]) {
  const adjacency = new Map(nodes.map((node) => [node.id, [] as string[]]));
  const indegree = new Map(nodes.map((node) => [node.id, 0]));
  edges.forEach((edge) => {
    if (edge.from === edge.to || !adjacency.has(edge.from) || !indegree.has(edge.to)) return;
    adjacency.get(edge.from)?.push(edge.to);
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  });

  const roots = nodes.filter((node) => indegree.get(node.id) === 0);
  const queue = (roots.length ? roots : nodes.slice(0, 1)).map((node) => node.id);
  const depths = new Map(queue.map((id) => [id, 0]));
  while (queue.length) {
    const current = queue.shift();
    if (!current) break;
    for (const target of adjacency.get(current) ?? []) {
      if (depths.has(target)) continue;
      depths.set(target, (depths.get(current) ?? 0) + 1);
      queue.push(target);
    }
  }

  let fallbackDepth = Math.max(0, ...depths.values()) + 1;
  nodes.forEach((node) => {
    if (!depths.has(node.id)) depths.set(node.id, fallbackDepth++);
  });
  const groups = new Map<number, DiagramNode[]>();
  nodes.forEach((node) => {
    const depth = depths.get(node.id) ?? 0;
    groups.set(depth, [...(groups.get(depth) ?? []), node]);
  });

  const maxPerLevel = Math.max(1, ...[...groups.values()].map((group) => group.length));
  const width = Math.max(760, maxPerLevel * 220);
  const maxDepth = Math.max(0, ...groups.keys());
  const height = Math.max(270, 130 + maxDepth * 150);
  const positioned: PositionedNode[] = [];
  groups.forEach((group, depth) => {
    group.forEach((node, index) => {
      positioned.push({
        ...node,
        depth,
        x: ((index + 1) * width) / (group.length + 1),
        y: 65 + depth * 150,
      });
    });
  });
  return { width, height, nodes: positioned };
}

function shortDiagramText(value: string, maxLength: number) {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
}

function NodeFlowDiagram({
  nodes,
  edges,
  markerId,
  label,
}: {
  nodes: DiagramNode[];
  edges: DiagramEdge[];
  markerId: string;
  label: string;
}) {
  const layout = createDiagramLayout(nodes, edges);
  const nodeMap = new Map(layout.nodes.map((node) => [node.id, node]));
  const nodeWidth = 180;
  const nodeHeight = 68;

  return (
    <div className="flowchart-scroll">
      <svg
        className="node-flowchart"
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        style={{ minWidth: `${Math.min(layout.width, 760)}px` }}
        role="img"
        aria-label={label}
      >
        <defs>
          <marker id={markerId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L8,4 L0,8 Z" />
          </marker>
        </defs>

        <g className="diagram-edges">
          {edges.map((edge, index) => {
            const source = nodeMap.get(edge.from);
            const target = nodeMap.get(edge.to);
            if (!source || !target) return null;
            let path = '';
            let labelX = (source.x + target.x) / 2;
            let labelY = (source.y + target.y) / 2;

            if (source.id === target.id) {
              const selfLoopIndex = edges.slice(0, index).filter((item) => item.from === edge.from && item.to === edge.to).length;
              const direction = source.x > layout.width * 0.7
                ? -1
                : source.x < layout.width * 0.3 ? 1 : selfLoopIndex % 2 === 0 ? 1 : -1;
              const reach = 78 + Math.floor(selfLoopIndex / 2) * 30;
              const side = source.x + direction * (nodeWidth / 2 - 5);
              path = `M ${side} ${source.y - 18} C ${side + direction * reach} ${source.y - 72}, ${side + direction * reach} ${source.y + 72}, ${side} ${source.y + 18}`;
              labelX = side + direction * (reach - 17);
              labelY = source.y;
            } else if (source.depth === target.depth) {
              const direction = target.x > source.x ? 1 : -1;
              const sourceX = source.x + direction * nodeWidth / 2;
              const targetX = target.x - direction * nodeWidth / 2;
              const arcY = Math.min(source.y, target.y) - 65 - (index % 2) * 16;
              path = `M ${sourceX} ${source.y} C ${sourceX + direction * 35} ${arcY}, ${targetX - direction * 35} ${arcY}, ${targetX} ${target.y}`;
              labelY = arcY + 3;
            } else {
              const downward = target.y > source.y;
              const sourceY = source.y + (downward ? nodeHeight / 2 : -nodeHeight / 2);
              const targetY = target.y + (downward ? -nodeHeight / 2 : nodeHeight / 2);
              const middleY = (sourceY + targetY) / 2;
              path = `M ${source.x} ${sourceY} C ${source.x} ${middleY}, ${target.x} ${middleY}, ${target.x} ${targetY}`;
              labelY = middleY;
            }

            const displayLabel = shortDiagramText(edge.label, 18);
            const labelWidth = Math.max(62, Math.min(164, [...displayLabel].length * 9 + 20));
            return (
              <g className="diagram-edge" key={`${edge.from}-${edge.to}-${index}`}>
                <title>{`${nodeMap.get(edge.from)?.title ?? edge.from} — ${edge.label} → ${nodeMap.get(edge.to)?.title ?? edge.to}`}</title>
                <path d={path} markerEnd={`url(#${markerId})`} />
                <g className="edge-label" transform={`translate(${labelX}, ${labelY})`}>
                  <rect x={-labelWidth / 2} y="-12" width={labelWidth} height="24" rx="12" />
                  <text textAnchor="middle" dominantBaseline="central">{displayLabel}</text>
                </g>
              </g>
            );
          })}
        </g>

        <g className="diagram-nodes">
          {layout.nodes.map((node, index) => (
            <g className="diagram-node" transform={`translate(${node.x}, ${node.y})`} key={node.id}>
              <title>{node.subtitle ? `${node.title}：${node.subtitle}` : node.title}</title>
              <rect x={-nodeWidth / 2} y={-nodeHeight / 2} width={nodeWidth} height={nodeHeight} rx="13" />
              <circle cx={-nodeWidth / 2 + 17} cy={-nodeHeight / 2 + 17} r="9" />
              <text className="node-index" x={-nodeWidth / 2 + 17} y={-nodeHeight / 2 + 17} textAnchor="middle" dominantBaseline="central">{index + 1}</text>
              <text className="node-title" x="0" y={node.subtitle ? -5 : 2} textAnchor="middle">{shortDiagramText(node.title, 16)}</text>
              {node.subtitle && <text className="node-subtitle" x="0" y="16" textAnchor="middle">{shortDiagramText(node.subtitle, 24)}</text>}
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
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
  const [skillOptions, setSkillOptions] = useState<SkillOption[]>([]);
  const [requestedSkill, setRequestedSkill] = useState('auto');
  const [skillChoice, setSkillChoice] = useState('');
  const [skillSubmitting, setSkillSubmitting] = useState(false);
  const [approvalSubmitting, setApprovalSubmitting] = useState(false);
  const [interactionSubmitting, setInteractionSubmitting] = useState(false);
  const [showInteractionFeedback, setShowInteractionFeedback] = useState(false);
  const [interactionFeedback, setInteractionFeedback] = useState('');
  const [taskRuns, setTaskRuns] = useState<TaskSummary[]>([]);
  const [petOpen, setPetOpen] = useState(false);
  const [petReminder, setPetReminder] = useState('');
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false);
  const [workspaceDraft, setWorkspaceDraft] = useState('examples/calculator');
  const [workspaceListing, setWorkspaceListing] = useState<WorkspaceListing>();
  const [workspacePickerError, setWorkspacePickerError] = useState('');
  const [workspacePickerLoading, setWorkspacePickerLoading] = useState(false);
  const streamRef = useRef<EventSource | null>(null);
  const conversationRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const approvalAllowRef = useRef<HTMLButtonElement | null>(null);
  const interactionApproveRef = useRef<HTMLButtonElement | null>(null);
  const taskStatusesRef = useRef<Map<string, string>>(new Map());
  const taskListReadyRef = useRef(false);
  const reminderTimerRef = useRef<number | undefined>(undefined);
  const knownActiveWorkspace = PROJECT_WORKSPACES.find((item) => item.path === workspace);
  const activeWorkspace = knownActiveWorkspace ?? {
    icon: '⌁',
    name: workspace === '.' ? '工作区根目录' : workspace.split('/').filter(Boolean).at(-1) ?? '本地工作区',
    path: workspace,
    stack: '本地文件夹',
  };
  const busyWorkspacePaths = new Set(
    taskRuns.filter((run) => isTaskActive(run.status)).map((run) => run.workspace),
  );
  if (runId && isActiveStatus(runStatus)) busyWorkspacePaths.add(workspace);
  const workspaceIsBusy = (candidate: string) => [...busyWorkspacePaths]
    .some((active) => workspacePathsOverlap(active, candidate));
  const activeTaskCount = taskRuns.filter((run) => isTaskActive(run.status)).length;
  const attentionTaskCount = taskRuns.filter((run) => needsAttention(run.status)).length;
  const failedTaskCount = taskRuns.filter((run) => run.status === 'failed').length;
  const petMood = attentionTaskCount > 0 ? 'attention' : activeTaskCount > 0 ? 'working' : failedTaskCount > 0 ? 'concerned' : 'resting';
  const petMessage = attentionTaskCount > 0
    ? `${attentionTaskCount} 个任务在等你确认`
    : activeTaskCount > 0
      ? `${activeTaskCount} 个任务正在推进`
      : taskRuns.length > 0 ? '所有任务都安静下来啦' : '交给我几个任务吧';

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
      if (event.type === 'skill_confirmation_requested') {
        setSkillChoice(String(event.payload?.recommended ?? ''));
        setRunStatus('waiting_skill_confirmation');
      }
      if (event.type === 'skill_confirmation_resolved') {
        setSkillSubmitting(false);
        setRunStatus('running');
      }
      if (event.type === 'approval_requested') setRunStatus('waiting_approval');
      if (event.type === 'approval_resolved') {
        setApprovalSubmitting(false);
        setRunStatus('running');
      }
      if (event.type === 'interaction_confirmation_requested') setRunStatus('waiting_interaction_confirmation');
      if (event.type === 'interaction_confirmation_resolved') {
        setInteractionSubmitting(false);
        setShowInteractionFeedback(false);
        setInteractionFeedback('');
        setRunStatus('running');
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
    fetch(`${API_BASE}/api/skills`)
      .then(async (response) => {
        if (!response.ok) throw new Error('skills unavailable');
        return await response.json() as { skills?: SkillOption[] };
      })
      .then((data) => setSkillOptions((data.skills ?? []).filter((skill) => skill.enabled)))
      .catch(() => setSkillOptions([]));
    return () => streamRef.current?.close();
  }, []);

  useEffect(() => {
    let disposed = false;
    const refreshTaskRuns = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/runs`);
        if (!response.ok) return;
        const data = await response.json() as { runs?: TaskSummary[] };
        if (disposed) return;
        const nextRuns = data.runs ?? [];
        if (taskListReadyRef.current) {
          const changed = nextRuns.find((run) => {
            const previous = taskStatusesRef.current.get(run.run_id);
            return previous && previous !== run.status
              && (needsAttention(run.status) || ['completed', 'failed'].includes(run.status));
          });
          if (changed) {
            const reminder = needsAttention(changed.status)
              ? `“${changed.task.slice(0, 18)}”需要你的确认。`
              : changed.status === 'completed'
                ? `“${changed.task.slice(0, 18)}”已经完成啦。`
                : `“${changed.task.slice(0, 18)}”遇到了问题。`;
            setPetReminder(reminder);
            if (reminderTimerRef.current) window.clearTimeout(reminderTimerRef.current);
            reminderTimerRef.current = window.setTimeout(() => setPetReminder(''), 8_000);
          }
        }
        taskStatusesRef.current = new Map(nextRuns.map((run) => [run.run_id, run.status]));
        taskListReadyRef.current = true;
        setTaskRuns(nextRuns);
      } catch {
        // The current task stream remains usable when the task summary endpoint is temporarily unavailable.
      }
    };
    void refreshTaskRuns();
    const interval = window.setInterval(() => void refreshTaskRuns(), 3_000);
    return () => {
      disposed = true;
      window.clearInterval(interval);
      if (reminderTimerRef.current) window.clearTimeout(reminderTimerRef.current);
    };
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
        setRequestedSkill(snapshot.requested_skill ?? 'auto');
        setEvents(restoredEvents);
        setSelectedId(restoredEvents.at(-1)?.event_id);
        if (restoredPlan) setPlan(restoredPlan.payload?.items as PlanItem[]);
        setRunStatus(restoredStatus);
        if (isActiveStatus(restoredStatus)) attachStream(snapshot.run_id);
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
  const selectedSkill = [...events].reverse().find((event) => event.type === 'skill_selected');
  const interactionFirst = selectedSkill?.payload?.skill === 'frontend_build'
    || events.some((event) => event.type.startsWith('interaction_'));
  const visiblePhases = interactionFirst
    ? PHASES
    : PHASES.filter((phase) => !['interaction_modeling', 'interaction_confirmation'].includes(phase.id));
  const currentPhase = [...events].reverse()
    .map((event) => event.phase)
    .find((phase) => visiblePhases.some((item) => item.id === phase)) ?? 'created';
  const currentPhaseIndex = visiblePhases.findIndex((phase) => phase.id === currentPhase);
  const finalEvent = [...events].reverse().find((event) => event.type === 'run_finished');
  const completedSteps = plan.filter((item) => item.status === 'success').length;
  const changedFiles = [...new Set(events.filter((event) => event.type === 'file_changed').map((event) => String(event.payload?.path ?? '')))].filter(Boolean);
  const resolvedApprovalIds = new Set(events
    .filter((event) => event.type === 'approval_resolved')
    .map((event) => String(event.payload?.approval_id ?? '')));
  const pendingApproval = [...events].reverse().find(
    (event) => event.type === 'approval_requested'
      && !resolvedApprovalIds.has(String(event.payload?.approval_id ?? '')),
  );
  const pendingArguments = pendingApproval?.payload?.arguments;
  const pendingTarget = pendingArguments && typeof pendingArguments === 'object'
    ? String((pendingArguments as Record<string, unknown>).path ?? (pendingArguments as Record<string, unknown>).command ?? '')
    : '';
  const resolvedSkillSelectionIds = new Set(events
    .filter((event) => event.type === 'skill_confirmation_resolved')
    .map((event) => String(event.payload?.selection_id ?? '')));
  const pendingSkillSelection = [...events].reverse().find(
    (event) => event.type === 'skill_confirmation_requested'
      && !resolvedSkillSelectionIds.has(String(event.payload?.selection_id ?? '')),
  );
  const skillCandidates = Array.isArray(pendingSkillSelection?.payload?.candidates)
    ? pendingSkillSelection.payload.candidates as SkillCandidate[]
    : [];
  const pendingSkillSelectionId = String(pendingSkillSelection?.payload?.selection_id ?? '');
  const recommendedSkill = String(pendingSkillSelection?.payload?.recommended ?? '');
  const resolvedInteractionIds = new Set(events
    .filter((event) => event.type === 'interaction_confirmation_resolved')
    .map((event) => String(event.payload?.model_id ?? '')));
  const pendingInteraction = [...events].reverse().find(
    (event) => event.type === 'interaction_confirmation_requested'
      && !resolvedInteractionIds.has(String(event.payload?.model_id ?? '')),
  );
  const latestInteractionEvent = [...events].reverse().find((event) => event.type === 'interaction_model_created');
  const interactionModel = latestInteractionEvent?.payload as InteractionModel | undefined;
  const latestInteractionResolution = [...events].reverse().find(
    (event) => event.type === 'interaction_confirmation_resolved'
      && event.payload?.model_id === interactionModel?.model_id,
  );
  const interactionModelState = pendingInteraction
    ? 'pending'
    : latestInteractionResolution?.payload?.decision === 'approve' ? 'confirmed' : 'revising';
  const interactionFlowNodes: DiagramNode[] = (interactionModel?.pages ?? []).map((page) => ({
    id: page.id,
    title: page.name,
    subtitle: page.purpose,
  }));
  const interactionFlowEdges: DiagramEdge[] = (interactionModel?.flows ?? []).map((flow) => ({
    from: flow.from,
    to: flow.to,
    label: flow.action,
  }));
  const interactionStateIds = [...new Set((interactionModel?.states ?? []).flatMap((state) => [state.from, state.to]))];
  const interactionStateNodes: DiagramNode[] = interactionStateIds.map((id) => ({ id, title: id }));
  const interactionStateEdges: DiagramEdge[] = (interactionModel?.states ?? []).map((state) => ({
    from: state.from,
    to: state.to,
    label: state.event,
  }));
  const skillTypes = ['skill_candidates', 'skill_confirmation_requested', 'skill_confirmation_resolved', 'skill_selected'];
  const interactionTypes = ['interaction_context_collected', 'interaction_model_created', 'interaction_confirmation_requested', 'interaction_confirmation_resolved'];
  const activityEvents = events.filter((event) => [...skillTypes, 'phase_changed', 'tool_finished', 'file_changed', 'quality_checkpoint', 'approval_requested', 'approval_resolved', 'error', ...interactionTypes].includes(event.type));
  const chatEvents = events.filter((event) => [...skillTypes, 'tool_finished', 'file_changed', 'quality_checkpoint', 'approval_requested', 'approval_resolved', 'error', ...interactionTypes].includes(event.type));

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

  async function openTask(selectedRun: TaskSummary) {
    setPetOpen(false);
    if (selectedRun.run_id === runId) return;
    streamRef.current?.close();
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/runs/${selectedRun.run_id}`);
      if (!response.ok) throw new Error(await response.text());
      const snapshot = await response.json() as RunSnapshot;
      const restoredEvents = Array.isArray(snapshot.events) ? snapshot.events : [];
      const restoredPlan = [...restoredEvents].reverse().find(
        (event) => event.type === 'plan_updated' && Array.isArray(event.payload?.items),
      );
      const restoredStatus = normalizeRunStatus(snapshot.status);
      setRunId(snapshot.run_id);
      setSubmittedTask(snapshot.task);
      setWorkspace(snapshot.workspace);
      setRequestedSkill(snapshot.requested_skill ?? 'auto');
      setEvents(restoredEvents);
      setSelectedId(restoredEvents.at(-1)?.event_id);
      setPlan(restoredPlan ? restoredPlan.payload?.items as PlanItem[] : IDLE_PLAN);
      setRunStatus(restoredStatus);
      setInteractionFeedback('');
      setShowInteractionFeedback(false);
      setSkillChoice('');
      sessionStorage.setItem(RUN_SESSION_KEY, JSON.stringify({
        runId: snapshot.run_id,
        task: snapshot.task,
        workspace: snapshot.workspace,
      } satisfies StoredRun));
      if (isActiveStatus(restoredStatus)) attachStream(snapshot.run_id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '无法打开任务');
    }
  }

  async function startRun() {
    const submitted = task.trim();
    if (!submitted || isActiveStatus(runStatus)) return;
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
        body: JSON.stringify({ task: submitted, workspace: workspace.trim() || '.', skill: requestedSkill }),
      });
      if (!response.ok) throw new Error(await response.text());
      const data = await response.json() as { run_id: string };
      sessionStorage.setItem(RUN_SESSION_KEY, JSON.stringify({
        runId: data.run_id,
        task: submitted,
        workspace: workspace.trim() || '.',
      } satisfies StoredRun));
      setTask('');
      setInteractionFeedback('');
      setShowInteractionFeedback(false);
      setSkillChoice('');
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

  const decideSkillSelection = useCallback(async () => {
    const selected = skillChoice || recommendedSkill;
    if (!runId || !pendingSkillSelectionId || !selected || skillSubmitting) return;
    setSkillSubmitting(true);
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/runs/${runId}/skill-selection/${pendingSkillSelectionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_name: selected }),
      });
      if (!response.ok) throw new Error(await response.text());
    } catch (error) {
      setSkillSubmitting(false);
      setNotice(error instanceof Error ? error.message : 'Skill 选择确认失败');
    }
  }, [pendingSkillSelectionId, recommendedSkill, runId, skillChoice, skillSubmitting]);

  const decideApproval = useCallback(async (decision: 'allow' | 'deny') => {
    const approvalId = String(pendingApproval?.payload?.approval_id ?? '');
    if (!runId || !approvalId || approvalSubmitting) return;
    setApprovalSubmitting(true);
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/runs/${runId}/approvals/${approvalId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
      if (!response.ok) throw new Error(await response.text());
    } catch (error) {
      setApprovalSubmitting(false);
      setNotice(error instanceof Error ? error.message : '授权操作失败');
    }
  }, [approvalSubmitting, pendingApproval, runId]);

  const decideInteraction = useCallback(async (decision: 'approve' | 'revise', feedback = '') => {
    const modelId = String(pendingInteraction?.payload?.model_id ?? '');
    if (!runId || !modelId || interactionSubmitting) return;
    const normalizedFeedback = feedback.trim();
    if (decision === 'revise' && !normalizedFeedback) {
      setNotice('请先说明希望怎样调整交互流程。');
      return;
    }
    setInteractionSubmitting(true);
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/runs/${runId}/interaction/${modelId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, feedback: normalizedFeedback }),
      });
      if (!response.ok) throw new Error(await response.text());
    } catch (error) {
      setInteractionSubmitting(false);
      setNotice(error instanceof Error ? error.message : '交互流程确认失败');
    }
  }, [interactionSubmitting, pendingInteraction, runId]);

  useEffect(() => {
    if (runStatus !== 'waiting_approval' || !pendingApproval) return;
    approvalAllowRef.current?.focus();

    const handleApprovalKey = (event: globalThis.KeyboardEvent) => {
      if (event.isComposing || event.repeat || approvalSubmitting) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        void decideApproval('deny');
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey) {
        if (event.target instanceof HTMLButtonElement) return;
        event.preventDefault();
        void decideApproval('allow');
      }
    };
    window.addEventListener('keydown', handleApprovalKey);
    return () => window.removeEventListener('keydown', handleApprovalKey);
  }, [approvalSubmitting, decideApproval, pendingApproval, runStatus]);

  useEffect(() => {
    if (runStatus !== 'waiting_skill_confirmation' || !pendingSkillSelection) return;
    const handleSkillKey = (event: globalThis.KeyboardEvent) => {
      if (event.isComposing || event.repeat || skillSubmitting) return;
      if (event.key === 'Enter' && !event.shiftKey) {
        if (event.target instanceof HTMLButtonElement || event.target instanceof HTMLSelectElement) return;
        event.preventDefault();
        void decideSkillSelection();
      }
    };
    window.addEventListener('keydown', handleSkillKey);
    return () => window.removeEventListener('keydown', handleSkillKey);
  }, [decideSkillSelection, pendingSkillSelection, runStatus, skillSubmitting]);

  useEffect(() => {
    if (runStatus !== 'waiting_interaction_confirmation' || !pendingInteraction) return;
    if (!showInteractionFeedback) interactionApproveRef.current?.focus();

    const handleInteractionKey = (event: globalThis.KeyboardEvent) => {
      if (event.isComposing || event.repeat || interactionSubmitting) return;
      if (event.key === 'Escape' && showInteractionFeedback) {
        event.preventDefault();
        setShowInteractionFeedback(false);
        setInteractionFeedback('');
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey && !showInteractionFeedback) {
        if (event.target instanceof HTMLButtonElement) return;
        event.preventDefault();
        void decideInteraction('approve');
      }
    };
    window.addEventListener('keydown', handleInteractionKey);
    return () => window.removeEventListener('keydown', handleInteractionKey);
  }, [decideInteraction, interactionSubmitting, pendingInteraction, runStatus, showInteractionFeedback]);

  async function resetDemo() {
    try {
      const response = await fetch(`${API_BASE}/api/demo/reset?workspace=${encodeURIComponent(workspace)}`, { method: 'POST' });
      if (!response.ok) throw new Error(await response.text());
      setNotice(`${activeWorkspace.name} 工作区已恢复，可以重新运行任务。`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '重置失败');
    }
  }

  async function loadWorkspaceDirectory(path: string) {
    setWorkspacePickerLoading(true);
    setWorkspacePickerError('');
    try {
      const response = await fetch(`${API_BASE}/api/workspaces?path=${encodeURIComponent(path.trim() || '.')}`);
      const data = await response.json() as WorkspaceListing & { detail?: string };
      if (!response.ok) throw new Error(data.detail || '无法读取该文件夹');
      setWorkspaceListing(data);
      setWorkspaceDraft(data.current);
    } catch (error) {
      setWorkspacePickerError(error instanceof Error ? error.message : '无法读取该文件夹');
    } finally {
      setWorkspacePickerLoading(false);
    }
  }

  function newRun() {
    setPetOpen(false);
    setWorkspaceDraft(workspace);
    setWorkspacePickerError('');
    setWorkspacePickerOpen(true);
    void loadWorkspaceDirectory(workspace);
  }

  async function confirmNewWorkspace() {
    const requestedWorkspace = workspaceDraft.trim() || '.';
    if (workspaceIsBusy(requestedWorkspace)) {
      setWorkspacePickerError('该工作区已有任务正在运行，请选择另一个文件夹。');
      return;
    }

    setWorkspacePickerLoading(true);
    setWorkspacePickerError('');
    try {
      const response = await fetch(`${API_BASE}/api/workspaces?path=${encodeURIComponent(requestedWorkspace)}`);
      const data = await response.json() as WorkspaceListing & { detail?: string };
      if (!response.ok) throw new Error(data.detail || '无法选择该工作区');
      if (workspaceIsBusy(data.current)) throw new Error('该工作区与一个运行中的任务目录重叠，请选择另一个文件夹。');

      const previousTaskContinues = Boolean(runId && isActiveStatus(runStatus));
      const selectedName = PROJECT_WORKSPACES.find((item) => item.path === data.current)?.name
        ?? (data.current === '.' ? '工作区根目录' : data.current.split('/').filter(Boolean).at(-1) ?? data.current);

      streamRef.current?.close();
      sessionStorage.removeItem(RUN_SESSION_KEY);
      setWorkspace(data.current);
      setTask('');
      setSubmittedTask('');
      setRunId(undefined);
      setRunStatus('idle');
      setEvents([]);
      setPlan(IDLE_PLAN);
      setSelectedId(undefined);
      setNotice(
        previousTaskContinues
          ? `原任务会继续在后台运行，已为新任务选择 ${selectedName} 工作区。`
          : `已选择 ${selectedName} 工作区，请输入新任务需求。`,
      );
      setInteractionFeedback('');
      setShowInteractionFeedback(false);
      setSkillChoice('');
      setWorkspacePickerOpen(false);
      window.setTimeout(() => composerRef.current?.focus(), 0);
    } catch (error) {
      setWorkspacePickerError(error instanceof Error ? error.message : '无法选择该工作区');
    } finally {
      setWorkspacePickerLoading(false);
    }
  }

  function clearCurrentRunForWorkspace(nextWorkspace: string) {
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
    setInteractionFeedback('');
    setShowInteractionFeedback(false);
    setSkillChoice('');
    setPetOpen(false);
  }

  function selectWorkspace(nextWorkspace: string) {
    if (nextWorkspace === workspace && !submittedTask) return;
    clearCurrentRunForWorkspace(nextWorkspace);
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
          <button className="new-run-button" type="button" onClick={newRun}>
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
              aria-pressed={workspace === project.path}
            >
              <span className="nav-icon">{project.icon}</span>
              <span><strong>{project.name}</strong><small>{project.stack}</small></span>
            </button>
          ))}
          {!knownActiveWorkspace && (
            <button className="nav-item active" type="button" aria-pressed="true">
              <span className="nav-icon">⌁</span>
              <span><strong>{activeWorkspace.name}</strong><small>{workspace}</small></span>
            </button>
          )}

          <p className="sidebar-section-label">Agent 设置</p>
          <Link className="nav-item" href="/skills">
            <span className="nav-icon">S</span>
            <span>
              <strong>Skill 管理</strong>
              <small>添加、启用或停用能力</small>
            </span>
          </Link>
          <Link className="nav-item" href="/settings">
            <span className="nav-icon">⚙</span>
            <span>
              <strong>Agent 设置</strong>
              <small>模式、工作流与执行预算</small>
            </span>
          </Link>

          <p className="sidebar-section-label">演示控制</p>
          <button className="nav-item" type="button" onClick={resetDemo} disabled={isActiveStatus(runStatus) || !knownActiveWorkspace}>
            <span className="nav-icon">↻</span>
            <span>
              <strong>重置演示项目</strong>
              <small>{knownActiveWorkspace ? `恢复 ${activeWorkspace.name} 的初始故障` : '仅预置演示工作区可重置'}</small>
            </span>
          </button>

        </nav>

        <div className="pet-companion">
          <button type="button" className={`pet-companion-button ${petMood}`} onClick={() => setPetOpen((open) => !open)} aria-expanded={petOpen} aria-controls="tracepet-task-center">
            <span className="pet-avatar" aria-hidden="true">ʕ•ᴥ•ʔ<i /></span>
            <span className="pet-companion-copy"><strong>TracePet</strong><small>{petMessage}</small></span>
            <span className="pet-task-count">{activeTaskCount}</span>
          </button>
        </div>

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

      {workspacePickerOpen && (
        <>
          <button className="workspace-picker-backdrop" type="button" onClick={() => setWorkspacePickerOpen(false)} aria-label="关闭工作区选择器" />
          <section className="workspace-picker" role="dialog" aria-modal="true" aria-labelledby="workspace-picker-title">
            <header>
              <div><span aria-hidden="true">⌁</span><div><small>NEW TASK</small><h2 id="workspace-picker-title">选择本地工作区</h2></div></div>
              <button type="button" onClick={() => setWorkspacePickerOpen(false)} aria-label="关闭">×</button>
            </header>

            <div className="workspace-picker-body">
              <aside>
                <small>预置工作区</small>
                <div className="workspace-preset-list">
                  {PROJECT_WORKSPACES.map((project) => {
                    const busy = workspaceIsBusy(project.path);
                    return (
                      <button
                        type="button"
                        className={workspaceDraft === project.path ? 'selected' : ''}
                        onClick={() => void loadWorkspaceDirectory(project.path)}
                        disabled={busy || workspacePickerLoading}
                        key={project.path}
                      >
                        <span>{project.icon}</span>
                        <span><strong>{project.name}</strong><small>{project.path}</small></span>
                        {busy && <em>运行中</em>}
                      </button>
                    );
                  })}
                </div>
                <p>不同工作区可并行运行；同一工作区同时只允许一个任务。</p>
              </aside>

              <div className="workspace-browser">
                <div className="workspace-root-line"><span>本地根目录</span><code>{workspaceListing?.root_path ?? '正在连接…'}</code></div>
                <form className="workspace-path-form" onSubmit={(event) => { event.preventDefault(); void loadWorkspaceDirectory(workspaceDraft); }}>
                  <label htmlFor="workspace-path">工作区路径</label>
                  <div><input id="workspace-path" value={workspaceDraft} onChange={(event) => setWorkspaceDraft(event.target.value)} placeholder="例如 examples/my-project" autoComplete="off" /><button type="submit" disabled={workspacePickerLoading}>定位</button></div>
                </form>

                {workspacePickerError && <div className="workspace-picker-error" role="alert"><span>!</span>{workspacePickerError}</div>}

                <div className="workspace-directory-head"><strong>文件夹</strong><small>{workspaceListing?.current ?? '.'}</small></div>
                <div className="workspace-directory-list" aria-busy={workspacePickerLoading}>
                  {workspaceListing?.parent !== null && workspaceListing && (
                    <button type="button" onClick={() => void loadWorkspaceDirectory(workspaceListing.parent ?? '.')} disabled={workspacePickerLoading}>
                      <span className="folder-icon">↰</span><span><strong>返回上一级</strong><small>{workspaceListing.parent ?? '.'}</small></span>
                    </button>
                  )}
                  {workspaceListing?.directories.map((directory) => {
                    const busy = workspaceIsBusy(directory.path);
                    return (
                      <button type="button" onClick={() => void loadWorkspaceDirectory(directory.path)} disabled={workspacePickerLoading} key={directory.path}>
                        <span className="folder-icon">▰</span><span><strong>{directory.name}</strong><small>{directory.path}</small></span>{busy && <em>任务运行中</em>}
                      </button>
                    );
                  })}
                  {!workspacePickerLoading && workspaceListing?.directories.length === 0 && <div className="workspace-directory-empty">这个文件夹中没有可继续浏览的子目录</div>}
                  {workspacePickerLoading && <div className="workspace-directory-empty">正在读取本地文件夹…</div>}
                </div>
              </div>
            </div>

            <footer>
              <div><small>当前选择</small><code>{workspaceDraft || '.'}</code></div>
              <button type="button" onClick={() => setWorkspacePickerOpen(false)}>取消</button>
              <button type="button" className="confirm-workspace" onClick={() => void confirmNewWorkspace()} disabled={workspacePickerLoading || workspaceIsBusy(workspaceDraft.trim() || '.')}>
                选择此工作区
              </button>
            </footer>
          </section>
        </>
      )}

      <button type="button" className={`pet-mobile-launch ${petMood}`} onClick={() => setPetOpen(true)} aria-label={`打开 TracePet，${petMessage}`}>
        <span aria-hidden="true">ʕ•ᴥ•ʔ</span><i>{activeTaskCount}</i>
      </button>

      {petOpen && (
        <>
          <button className="pet-panel-backdrop" type="button" onClick={() => setPetOpen(false)} aria-label="关闭任务中心" />
          <aside className="pet-task-center" id="tracepet-task-center" role="dialog" aria-modal="false" aria-labelledby="tracepet-title">
            <header>
              <div><span className={`pet-panel-avatar ${petMood}`} aria-hidden="true">ʕ•ᴥ•ʔ</span><div><small>YOUR TASK COMPANION</small><h2 id="tracepet-title">TracePet 任务中心</h2></div></div>
              <button type="button" onClick={() => setPetOpen(false)} aria-label="关闭">×</button>
            </header>
            <div className="pet-overview">
              <div><strong>{activeTaskCount}</strong><span>进行中</span></div>
              <div className={attentionTaskCount ? 'attention' : ''}><strong>{attentionTaskCount}</strong><span>待处理</span></div>
              <button type="button" onClick={newRun}><span>＋</span> 新建并行任务</button>
            </div>
            <div className="pet-task-list">
              {taskRuns.length === 0 ? (
                <div className="pet-empty"><span>🐾</span><strong>还没有任务</strong><p>创建任务后，我会在这里陪你盯住进度。</p></div>
              ) : taskRuns.map((run) => {
                const progress = run.total_steps > 0 ? Math.round((run.completed_steps / run.total_steps) * 100) : 0;
                return (
                  <button type="button" className={`pet-task-item ${run.status} ${run.run_id === runId ? 'current' : ''}`} onClick={() => void openTask(run)} key={run.run_id}>
                    <span className="pet-task-status-icon">{needsAttention(run.status) ? '!' : run.status === 'completed' ? '✓' : run.status === 'failed' ? '×' : '›'}</span>
                    <span className="pet-task-main">
                      <span className="pet-task-title"><strong>{run.task}</strong><em>{taskStatusLabel(run.status)}</em></span>
                      <small>{run.workspace} · {run.last_event?.title ?? '准备任务'}</small>
                      <span className="pet-progress-track"><i style={{ width: `${progress}%` }} /></span>
                      <span className="pet-progress-copy">{run.total_steps > 0 ? `${run.completed_steps}/${run.total_steps} 个步骤` : run.last_event?.summary ?? '等待开始'}</span>
                    </span>
                  </button>
                );
              })}
            </div>
            <footer><span>🐾</span><p>任务会继续在后台运行。需要确认、完成或失败时，我会提醒你。</p></footer>
          </aside>
        </>
      )}

      {petReminder && (
        <div className="pet-reminder" role="status">
          <span aria-hidden="true">ʕ•ᴥ•ʔ</span>
          <div><strong>TracePet 提醒</strong><p>{petReminder}</p></div>
          <button type="button" onClick={() => { setPetOpen(true); setPetReminder(''); }}>查看</button>
          <button type="button" onClick={() => setPetReminder('')} aria-label="关闭提醒">×</button>
        </div>
      )}

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
                      ? interactionFirst
                        ? `已加载 ${selectedSkill.title}。我会先输出终端用户交互流程，得到你的确认后再开始实现。`
                        : `已加载 ${selectedSkill.title}。我会先理解项目并复现问题，然后实施最小修改并运行验证。`
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
                          <span className={`mini-event-icon ${event.status}`}>{eventIcon(event)}</span>
                          <span><strong>{event.title}</strong><small>{event.summary}</small></span>
                          <time>{formatTime(event.timestamp)}</time>
                        </button>
                      ))}
                    </div>
                  )}

                  {interactionModel && (
                    <section className={`interaction-card ${interactionModelState}`} aria-live="polite">
                      <div className="interaction-card-head">
                        <span>F</span>
                        <div>
                          <small>INTERACTION MODEL · V{interactionModel.revision}</small>
                          <strong>{interactionModel.title}</strong>
                          <p>{interactionModel.summary}</p>
                        </div>
                        <em>{interactionModelState === 'pending' ? '待确认' : interactionModelState === 'confirmed' ? '已确认' : '正在调整'}</em>
                      </div>

                      <div className="interaction-section">
                        <div className="interaction-section-title"><strong>终端用户流程图</strong><small>{interactionFlowNodes.length} 个节点 · {interactionFlowEdges.length} 条路径</small></div>
                        <NodeFlowDiagram
                          nodes={interactionFlowNodes}
                          edges={interactionFlowEdges}
                          markerId="page-flow-arrow"
                          label={`${interactionModel.title} 页面流转图`}
                        />
                        <div className="flowchart-legend"><span><i className="node-sample" />页面或弹层</span><span><i className="edge-sample" />用户操作与流转方向</span></div>
                      </div>

                      <div className="interaction-columns">
                        <div className="interaction-section state-section">
                          <div className="interaction-section-title"><strong>核心状态机</strong><small>{interactionStateNodes.length} 个状态</small></div>
                          <NodeFlowDiagram
                            nodes={interactionStateNodes}
                            edges={interactionStateEdges}
                            markerId="state-flow-arrow"
                            label={`${interactionModel.title} 状态机图`}
                          />
                        </div>
                        <div className="interaction-section criteria-section">
                          <div className="interaction-section-title"><strong>验收标准</strong><small>{interactionModel.acceptance_criteria?.length ?? 0} 项</small></div>
                          <ul className="criteria-list">
                            {(interactionModel.acceptance_criteria ?? []).map((criterion, index) => <li key={`${criterion}-${index}`}><span>✓</span>{criterion}</li>)}
                          </ul>
                        </div>
                      </div>

                      {pendingInteraction && runStatus === 'waiting_interaction_confirmation' && (
                        <div className="interaction-card-controls">
                          <button type="button" className="revise-interaction" onClick={() => setShowInteractionFeedback(true)} disabled={interactionSubmitting}>需要调整</button>
                          <button type="button" className="approve-interaction" onClick={() => void decideInteraction('approve')} disabled={interactionSubmitting}>
                            {interactionSubmitting ? '处理中…' : '符合预期，继续实现'}
                          </button>
                        </div>
                      )}
                    </section>
                  )}

                  {pendingApproval && runStatus === 'waiting_approval' && (
                    <section className="approval-card" aria-live="assertive">
                      <div className="approval-card-head">
                        <span>!</span>
                        <div>
                          <strong>Agent 请求临时授权</strong>
                          <small>仅允许下面这一项操作执行一次</small>
                        </div>
                        <em>{String(pendingApproval.payload?.risk ?? 'medium').toUpperCase()}</em>
                      </div>
                      <p>{pendingApproval.summary}</p>
                      <div className="approval-action">
                        <small>工具</small>
                        <strong>{String(pendingApproval.payload?.tool ?? 'unknown')}</strong>
                        <pre>{JSON.stringify(pendingApproval.payload?.arguments ?? {}, null, 2)}</pre>
                      </div>
                      <div className="approval-controls">
                        <button type="button" className="deny-approval" onClick={() => void decideApproval('deny')} disabled={approvalSubmitting}>拒绝 <kbd>Esc</kbd></button>
                        <button type="button" className="allow-approval" onClick={() => void decideApproval('allow')} disabled={approvalSubmitting}>
                          {approvalSubmitting ? '处理中…' : <>同意并继续 <kbd>Enter</kbd></>}
                        </button>
                      </div>
                    </section>
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
          {pendingSkillSelection && runStatus === 'waiting_skill_confirmation' ? (
            <section className="skill-selection-dock" role="dialog" aria-modal="false" aria-labelledby="skill-selection-title">
              <span className="skill-selection-dock-icon" aria-hidden="true">S</span>
              <div className="skill-selection-dock-copy">
                <strong id="skill-selection-title">确认本次任务使用的 Skill</strong>
                <small>
                  自动路由置信度 {Math.round(Number(pendingSkillSelection.payload?.confidence ?? 0) * 100)}%
                  {' · '}{String(pendingSkillSelection.payload?.reason ?? '请选择最符合任务的能力')}
                </small>
                <div className="skill-candidate-options" role="radiogroup" aria-label="Skill 候选项">
                  {skillCandidates.map((candidate) => (
                    <button
                      type="button"
                      role="radio"
                      aria-checked={(skillChoice || recommendedSkill) === candidate.name}
                      className={(skillChoice || recommendedSkill) === candidate.name ? 'selected' : ''}
                      onClick={() => setSkillChoice(candidate.name)}
                      disabled={skillSubmitting}
                      key={candidate.name}
                    >
                      <strong>{candidate.display_name}</strong>
                      <span>{candidate.description}</span>
                      <em>{candidate.name === recommendedSkill ? '推荐' : `${candidate.keyword_score} 分`}</em>
                    </button>
                  ))}
                </div>
              </div>
              <div className="skill-selection-dock-actions">
                <button type="button" className="cancel-revision" onClick={() => void cancelRun()} disabled={skillSubmitting}>停止任务</button>
                <button type="button" className="confirm-skill-selection" onClick={() => void decideSkillSelection()} disabled={skillSubmitting || !(skillChoice || recommendedSkill)}>
                  {skillSubmitting ? '处理中…' : <>使用此 Skill <kbd>Enter</kbd></>}
                </button>
              </div>
            </section>
          ) : pendingInteraction && runStatus === 'waiting_interaction_confirmation' ? (
            <section className={`interaction-dock ${showInteractionFeedback ? 'editing' : ''}`} role="dialog" aria-modal="false" aria-labelledby="interaction-dock-title">
              <span className="interaction-dock-icon" aria-hidden="true">F</span>
              <div className="interaction-dock-copy">
                <strong id="interaction-dock-title">确认终端用户交互流程</strong>
                {showInteractionFeedback ? (
                  <textarea
                    autoFocus
                    value={interactionFeedback}
                    onChange={(event) => setInteractionFeedback(event.target.value)}
                    placeholder="例如：增加设置页面；胜利提示不要跳转页面，改为棋盘上的弹层…"
                    rows={2}
                    aria-label="交互流程调整意见"
                  />
                ) : (
                  <small>确认后才会开始创建和修改代码；按 Enter 可以直接同意。</small>
                )}
              </div>
              <div className="interaction-dock-actions">
                {showInteractionFeedback ? (
                  <>
                    <button type="button" className="cancel-revision" onClick={() => { setShowInteractionFeedback(false); setInteractionFeedback(''); }} disabled={interactionSubmitting}>取消</button>
                    <button type="button" className="submit-revision" onClick={() => void decideInteraction('revise', interactionFeedback)} disabled={interactionSubmitting || !interactionFeedback.trim()}>
                      {interactionSubmitting ? '提交中…' : '提交调整意见'}
                    </button>
                  </>
                ) : (
                  <>
                    <button type="button" className="cancel-revision stop-interaction" onClick={() => void cancelRun()} disabled={interactionSubmitting}>停止任务</button>
                    <button type="button" className="revise-interaction" onClick={() => setShowInteractionFeedback(true)} disabled={interactionSubmitting}>需要调整</button>
                    <button ref={interactionApproveRef} type="button" className="approve-interaction" onClick={() => void decideInteraction('approve')} disabled={interactionSubmitting} aria-keyshortcuts="Enter">
                      {interactionSubmitting ? '处理中…' : <>符合预期，继续实现 <kbd>Enter</kbd></>}
                    </button>
                  </>
                )}
              </div>
            </section>
          ) : pendingApproval && runStatus === 'waiting_approval' ? (
            <section className="approval-dock" role="dialog" aria-modal="false" aria-labelledby="approval-dock-title">
              <span className="approval-dock-icon" aria-hidden="true">!</span>
              <div className="approval-dock-copy">
                <strong id="approval-dock-title">需要你的确认</strong>
                <small>
                  {String(pendingApproval.payload?.tool ?? '操作')}
                  {pendingTarget ? ` · ${pendingTarget}` : ''}
                  {' · 仅执行一次'}
                </small>
              </div>
              <div className="approval-dock-actions">
                <button type="button" className="deny-approval" onClick={() => void decideApproval('deny')} disabled={approvalSubmitting} aria-keyshortcuts="Escape">
                  拒绝 <kbd>Esc</kbd>
                </button>
                <button ref={approvalAllowRef} type="button" className="allow-approval" onClick={() => void decideApproval('allow')} disabled={approvalSubmitting} aria-keyshortcuts="Enter">
                  {approvalSubmitting ? '处理中…' : <>同意并继续 <kbd>Enter</kbd></>}
                </button>
              </div>
            </section>
          ) : (
            <div className="composer-card">
              <textarea
                ref={composerRef}
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
                <label className="composer-skill-picker" title="选择自动路由或手动指定 Skill">
                  <span>S</span>
                  <select value={requestedSkill} onChange={(event) => setRequestedSkill(event.target.value)} aria-label="任务 Skill">
                    <option value="auto">自动选择 Skill</option>
                    {skillOptions.map((skill) => (
                      <option value={skill.name} key={skill.name}>{skill.display_name}</option>
                    ))}
                  </select>
                </label>
                <span className="keyboard-hint">Enter 发送 · Shift + Enter 换行</span>
                {isActiveStatus(runStatus) ? (
                  <button className="stop-button" type="button" onClick={cancelRun} aria-label="停止任务">■</button>
                ) : (
                  <button className="send-button" type="button" onClick={startRun} disabled={!task.trim()} aria-label="开始运行">↑</button>
                )}
              </div>
            </div>
          )}
          <p className="composer-note">
            {runStatus === 'waiting_skill_confirmation'
              ? '自动路由不够确定，请选择一项 Skill；按 Enter 使用当前选项。'
              : runStatus === 'waiting_interaction_confirmation'
              ? '请先确认产品交互；未确认前 Agent 不会开始编写代码。'
              : runStatus === 'waiting_approval'
                ? '请审查上方操作；按 Enter 同意，按 Esc 拒绝。'
                : 'TraceCoder 会在本地受控工作区中读写文件并执行命令，请审查重要改动。'}
          </p>
        </div>
      </section>

      <aside className="inspector">
        <header className="inspector-header">
          <div><span className="panel-kicker">RUN DETAILS</span><h2>Agent 运行</h2></div>
          <span className={`live-indicator ${runStatus}`}><i />{inspectorStatusCopy(runStatus)}</span>
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
            <div className="section-title"><span>阶段</span><small>{Math.max(0, currentPhaseIndex + 1)}/{visiblePhases.length}</small></div>
            <div className="phase-strip" style={{ gridTemplateColumns: `repeat(${visiblePhases.length}, minmax(0, 1fr))` }}>
              {visiblePhases.map((phase, index) => (
                <div key={phase.id} className={`${phase.id === currentPhase ? 'active' : ''} ${currentPhase === 'completed' || currentPhaseIndex > index ? 'done' : ''}`}>
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
                  <span className="activity-icon">{eventIcon(event)}</span>
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
              <span className={`selected-event-icon ${activeEvent?.status ?? 'pending'}`}>{eventIcon(activeEvent)}</span>
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
