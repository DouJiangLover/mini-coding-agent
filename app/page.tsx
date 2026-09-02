'use client';

import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState } from 'react';

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
  covers?: string[];
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
  acceptance_criteria: Array<AcceptanceCriterion | string>;
};

type AcceptanceCriterion = {
  id: string;
  description: string;
  priority: 'must' | 'should';
  verification: 'automated_test' | 'build_check' | 'human_review';
};

type RequirementEvidence = {
  evidence_id: string;
  evidence_type: 'implementation' | 'verification' | 'review';
  tool: string;
  passed: boolean;
  summary: string;
  artifact?: string;
  command?: string;
  association_source: 'explicit' | 'plan_fallback';
  timestamp: string;
};

type TracedRequirement = AcceptanceCriterion & {
  requirement_id: string;
  status: 'pending' | 'implemented' | 'verified' | 'failed';
  evidence: RequirementEvidence[];
};

type TraceabilitySnapshot = {
  active: boolean;
  total: number;
  verified: number;
  coverage_percent: number;
  counts: Record<'pending' | 'implemented' | 'verified' | 'failed', number>;
  requirements: TracedRequirement[];
};

type HookPipelineSnapshot = {
  before?: { decision?: string; hook?: string };
  after?: string[];
};

type RunSnapshot = {
  run_id: string;
  task: string;
  root_task?: string;
  parent_run_id?: string | null;
  workspace: string;
  requested_skill?: string;
  status: string;
  events?: RunEvent[];
  conversation?: ConversationTurn[];
  session_id?: string;
  session_entry_id?: string;
  parent_entry_id?: string | null;
  session_tree?: SessionTree | null;
};

type ConversationTurn = {
  run_id: string;
  session_entry_id?: string;
  parent_entry_id?: string | null;
  task: string;
  status: string;
  summary: string;
  created_at: string;
};

type SessionTreeEntry = ConversationTurn & {
  entry_id: string;
  parent_id: string | null;
  root_task: string;
  changed_files: string[];
  successful_commands: string[];
  depth: number;
  child_count: number;
  active: boolean;
};

type SessionTree = {
  session_id: string;
  workspace: string;
  root_task: string;
  active_entry_id: string;
  entries: SessionTreeEntry[];
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
const RUN_SESSION_KEY = 'intentflow:last-run';
const LEGACY_RUN_SESSION_KEY = 'tracecoder:last-run';
const ACTIVE_TASK_STATUSES = ['created', 'running', 'waiting_skill_confirmation', 'waiting_approval', 'waiting_interaction_confirmation'];

const PHASES = [
  { id: 'interaction_modeling', label: '交互建模' },
  { id: 'interaction_confirmation', label: '确认流程' },
  { id: 'selecting_skill', label: '组合 Skill' },
  { id: 'planning', label: '制定计划' },
  { id: 'inspecting', label: '理解项目' },
  { id: 'reproducing', label: '建立基线' },
  { id: 'diagnosing', label: '定位根因' },
  { id: 'implementing', label: '实施修改' },
  { id: 'documenting', label: '编写说明' },
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
  { icon: '∑', name: 'Calculator', path: 'calculator', stack: 'Python · pytest' },
  { icon: '★', name: 'Star Catcher', path: 'star-catcher', stack: 'HTML · CSS · JavaScript' },
  { icon: '20', name: '2048 Game', path: '2048-game', stack: '需求文档 · 从零构建' },
  { icon: '✓', name: 'Approval Demo', path: 'approval-demo', stack: 'Python · 单次授权演示' },
  { icon: '!', name: 'Failure Lab', path: 'order-engine-lab', stack: 'Python · 复杂故障实验' },
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
  traceability_initialized: 'AC',
  traceability_updated: 'AC',
  user_guide_ready: 'R',
  steering_received: '↗',
  steering_applied: '✓',
  steering_deferred_completion: '↗',
  user_requirement_received: '✦',
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

function hookLabel(name: string) {
  return ({
    hook_pipeline: '执行前检查通过',
    argument_validation: '参数校验',
    repeated_call_guard: '重复动作守卫',
    quality_preflight: '质量前置关卡',
    user_guide_delivery: '终端用户文档关卡',
    traceability_evidence: '需求证据收集',
    run_observation: '运行状态归档',
  } as Record<string, string>)[name] ?? name;
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
type PositionedNode = DiagramNode & { x: number; y: number; depth: number; order: number };

const DIAGRAM_NODE_WIDTH = 210;
const DIAGRAM_NODE_HEIGHT = 92;
const DIAGRAM_COLUMN_GAP = 70;
const DIAGRAM_ROW_GAP = 92;
const DIAGRAM_MAX_COLUMNS = 3;

function createDiagramLayout(nodes: DiagramNode[], edges: DiagramEdge[]) {
  if (!nodes.length) return { width: 760, height: 240, nodes: [] as PositionedNode[], layers: [] as { depth: number; y: number }[] };
  const adjacency = new Map(nodes.map((node) => [node.id, [] as string[]]));
  const predecessors = new Map(nodes.map((node) => [node.id, [] as string[]]));
  const indegree = new Map(nodes.map((node) => [node.id, 0]));
  const originalIndex = new Map(nodes.map((node, index) => [node.id, index]));
  edges.forEach((edge) => {
    if (edge.from === edge.to || !adjacency.has(edge.from) || !indegree.has(edge.to)) return;
    const targets = adjacency.get(edge.from);
    if (targets && !targets.includes(edge.to)) {
      targets.push(edge.to);
      predecessors.get(edge.to)?.push(edge.from);
    }
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  });

  const depthById = new Map<string, number>();
  const queue: string[] = [];
  const roots = nodes.filter((node) => indegree.get(node.id) === 0);
  (roots.length ? roots : nodes.slice(0, 1)).forEach((node) => {
    depthById.set(node.id, 0);
    queue.push(node.id);
  });
  while (queue.length) {
    const current = queue.shift();
    if (!current) continue;
    const nextDepth = (depthById.get(current) ?? 0) + 1;
    [...(adjacency.get(current) ?? [])]
      .sort((first, second) => (originalIndex.get(first) ?? 0) - (originalIndex.get(second) ?? 0))
      .forEach((target) => {
        if (depthById.has(target)) return;
        depthById.set(target, nextDepth);
        queue.push(target);
      });
  }
  nodes.forEach((node) => {
    if (depthById.has(node.id)) return;
    depthById.set(node.id, 0);
    queue.push(node.id);
    while (queue.length) {
      const current = queue.shift();
      if (!current) continue;
      const nextDepth = (depthById.get(current) ?? 0) + 1;
      (adjacency.get(current) ?? []).forEach((target) => {
        if (depthById.has(target)) return;
        depthById.set(target, nextDepth);
        queue.push(target);
      });
    }
  });

  const maxDepth = Math.max(...depthById.values());
  const groupedIds = Array.from({ length: maxDepth + 1 }, (_, depth) => nodes
    .filter((node) => depthById.get(node.id) === depth)
    .sort((first, second) => (originalIndex.get(first.id) ?? 0) - (originalIndex.get(second.id) ?? 0))
    .map((node) => node.id));

  // A few forward/backward barycentric sweeps keep branches near their
  // parents and reduce crossings without adding a graph-layout SDK.
  for (let pass = 0; pass < 4; pass += 1) {
    for (let depth = 1; depth <= maxDepth; depth += 1) {
      const previousOrder = new Map(groupedIds[depth - 1].map((id, index) => [id, index]));
      groupedIds[depth].sort((first, second) => {
        const score = (id: string) => {
          const positions = (predecessors.get(id) ?? [])
            .map((parent) => previousOrder.get(parent))
            .filter((value): value is number => value !== undefined);
          return positions.length ? positions.reduce((sum, value) => sum + value, 0) / positions.length : originalIndex.get(id) ?? 0;
        };
        return score(first) - score(second) || (originalIndex.get(first) ?? 0) - (originalIndex.get(second) ?? 0);
      });
    }
    for (let depth = maxDepth - 1; depth >= 0; depth -= 1) {
      const nextOrder = new Map(groupedIds[depth + 1].map((id, index) => [id, index]));
      groupedIds[depth].sort((first, second) => {
        const score = (id: string) => {
          const positions = (adjacency.get(id) ?? [])
            .map((child) => nextOrder.get(child))
            .filter((value): value is number => value !== undefined);
          return positions.length ? positions.reduce((sum, value) => sum + value, 0) / positions.length : originalIndex.get(id) ?? 0;
        };
        return score(first) - score(second) || (originalIndex.get(first) ?? 0) - (originalIndex.get(second) ?? 0);
      });
    }
  }

  const widestLayer = Math.max(...groupedIds.map((group) => group.length));
  const columns = Math.min(DIAGRAM_MAX_COLUMNS, Math.max(1, widestLayer));
  const contentWidth = columns * DIAGRAM_NODE_WIDTH + Math.max(0, columns - 1) * DIAGRAM_COLUMN_GAP;
  const width = Math.max(820, contentWidth + 190);
  const rowStep = DIAGRAM_NODE_HEIGHT + DIAGRAM_ROW_GAP;
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const positioned: PositionedNode[] = [];
  const layerLayouts: { depth: number; y: number; startY: number; rows: number }[] = [];
  let cursorY = 92;
  groupedIds.forEach((ids, depth) => {
    const rows = Math.ceil(ids.length / DIAGRAM_MAX_COLUMNS);
    const startY = cursorY;
    ids.forEach((id, order) => {
      const row = Math.floor(order / DIAGRAM_MAX_COLUMNS);
      const rowStart = row * DIAGRAM_MAX_COLUMNS;
      const rowCount = Math.min(DIAGRAM_MAX_COLUMNS, ids.length - rowStart);
      const column = order % DIAGRAM_MAX_COLUMNS;
      const offset = (column - (rowCount - 1) / 2) * (DIAGRAM_NODE_WIDTH + DIAGRAM_COLUMN_GAP);
      positioned.push({
        ...(nodeById.get(id) as DiagramNode),
        depth,
        order,
        x: width / 2 + offset,
        y: startY + row * rowStep,
      });
    });
    layerLayouts.push({ depth, y: startY + ((rows - 1) * rowStep) / 2, startY, rows });
    cursorY += rows * rowStep + 46;
  });
  positioned.sort((first, second) => first.depth - second.depth || first.order - second.order);
  return {
    width,
    height: Math.max(270, cursorY - 46 + DIAGRAM_NODE_HEIGHT / 2 + 32),
    nodes: positioned,
    layers: layerLayouts.map(({ depth, y }) => ({ depth, y })),
  };
}

function diagramTextLines(value: string, lineLength: number, maxLines: number) {
  const normalized = value.replace(/\s+/g, ' ').trim();
  const lines: string[] = [];
  let cursor = 0;
  while (cursor < normalized.length && lines.length < maxLines) {
    lines.push(normalized.slice(cursor, cursor + lineLength));
    cursor += lineLength;
  }
  if (cursor < normalized.length && lines.length) {
    lines[lines.length - 1] = `${lines[lines.length - 1].slice(0, -1)}…`;
  }
  return lines;
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
  const [focusedNode, setFocusedNode] = useState<string>();
  const [zoom, setZoom] = useState(1);
  const [showSecondary, setShowSecondary] = useState(false);
  const renderedWidth = Math.round(layout.width * zoom);
  const validEdges = edges.filter((edge) => nodeMap.has(edge.from) && nodeMap.has(edge.to));
  const secondaryEdges = validEdges.filter((edge) => {
    const source = nodeMap.get(edge.from);
    const target = nodeMap.get(edge.to);
    return source && target && (source.id === target.id || target.depth <= source.depth);
  });
  const visibleEdges = showSecondary ? validEdges : validEdges.filter((edge) => !secondaryEdges.includes(edge));
  const focusedTitle = focusedNode ? nodeMap.get(focusedNode)?.title : undefined;

  return (
    <div className="flowchart-shell">
      <div className="flowchart-toolbar">
        <span>{focusedTitle ? `正在查看：${focusedTitle} 的直接流转` : '主路径 · 点击页面聚焦，回路按需展开'}</span>
        <div>
          {focusedNode && <button type="button" onClick={() => setFocusedNode(undefined)}>显示全部</button>}
          {secondaryEdges.length > 0 && (
            <button
              type="button"
              className="flowchart-toggle"
              onClick={() => setShowSecondary((value) => !value)}
              aria-pressed={showSecondary}
            >
              {showSecondary ? '隐藏回路' : `显示回路（${secondaryEdges.length}）`}
            </button>
          )}
          <button type="button" aria-label="缩小流程图" onClick={() => setZoom((value) => Math.max(.75, Number((value - .25).toFixed(2))))} disabled={zoom <= .75}>−</button>
          <output>{Math.round(zoom * 100)}%</output>
          <button type="button" aria-label="放大流程图" onClick={() => setZoom((value) => Math.min(1.5, Number((value + .25).toFixed(2))))} disabled={zoom >= 1.5}>＋</button>
        </div>
      </div>
      <div className="flowchart-scroll">
        <svg
          className="node-flowchart"
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          style={{ width: `${renderedWidth}px`, minWidth: '100%' }}
          role="img"
          aria-label={label}
        >
        <defs>
          <marker id={markerId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L8,4 L0,8 Z" />
          </marker>
        </defs>

        <g className="diagram-layers" aria-hidden="true">
          {layout.layers.map((layer) => (
            <g transform={`translate(0, ${layer.y})`} key={layer.depth}>
              <line x1="34" x2={layout.width - 34} />
              <text x="42" y={-DIAGRAM_NODE_HEIGHT / 2 - 15}>阶段 {layer.depth + 1}</text>
            </g>
          ))}
        </g>

        <g className="diagram-edges">
          {visibleEdges.map((edge) => {
            const source = nodeMap.get(edge.from);
            const target = nodeMap.get(edge.to);
            if (!source || !target) return null;
            const sourceEdges = visibleEdges.filter((item) => item.from === edge.from);
            const targetEdges = visibleEdges.filter((item) => item.to === edge.to);
            const sourceSlot = sourceEdges.indexOf(edge) - (sourceEdges.length - 1) / 2;
            const targetSlot = targetEdges.indexOf(edge) - (targetEdges.length - 1) / 2;
            const sourceOffset = sourceSlot * Math.min(22, 92 / Math.max(1, sourceEdges.length));
            const targetOffset = targetSlot * Math.min(22, 92 / Math.max(1, targetEdges.length));
            let path = '';
            let labelX = (source.x + target.x) / 2;
            let labelY = (source.y + target.y) / 2;

            if (source.id === target.id) {
              const selfLoopIndex = visibleEdges.slice(0, visibleEdges.indexOf(edge)).filter((item) => item.from === edge.from && item.to === edge.to).length;
              const direction = selfLoopIndex % 2 === 0 ? 1 : -1;
              const sourceX = source.x + direction * DIAGRAM_NODE_WIDTH / 2;
              const laneX = source.x + direction * (DIAGRAM_NODE_WIDTH / 2 + 60 + selfLoopIndex * 34);
              path = `M ${sourceX} ${source.y - 20} C ${laneX} ${source.y - 20}, ${laneX} ${source.y + 54}, ${sourceX} ${source.y + 20}`;
              labelX = laneX;
              labelY = source.y + 17;
            } else if (target.depth === source.depth + 1) {
              const sourceX = source.x + sourceOffset;
              const targetX = target.x + targetOffset;
              const sourceY = source.y + DIAGRAM_NODE_HEIGHT / 2;
              const targetY = target.y - DIAGRAM_NODE_HEIGHT / 2;
              const middleY = (sourceY + targetY) / 2;
              path = `M ${sourceX} ${sourceY} C ${sourceX} ${middleY}, ${targetX} ${middleY}, ${targetX} ${targetY}`;
              labelX = (sourceX + targetX) / 2;
              labelY = middleY;
            } else if (target.depth === source.depth) {
              const sameDepthIndex = visibleEdges.slice(0, visibleEdges.indexOf(edge)).filter((item) => {
                const itemSource = nodeMap.get(item.from);
                const itemTarget = nodeMap.get(item.to);
                return itemSource?.depth === source.depth && itemTarget?.depth === source.depth;
              }).length;
              const laneY = source.y - DIAGRAM_NODE_HEIGHT / 2 - 28 - (sameDepthIndex % 3) * 22;
              path = `M ${source.x + sourceOffset} ${source.y - DIAGRAM_NODE_HEIGHT / 2} C ${source.x + sourceOffset} ${laneY}, ${target.x + targetOffset} ${laneY}, ${target.x + targetOffset} ${target.y - DIAGRAM_NODE_HEIGHT / 2}`;
              labelX = (source.x + target.x) / 2;
              labelY = laneY;
            } else {
              const detourIndex = visibleEdges.slice(0, visibleEdges.indexOf(edge)).filter((item) => {
                const detourSource = nodeMap.get(item.from);
                const detourTarget = nodeMap.get(item.to);
                return Boolean(
                  detourSource
                  && detourTarget
                  && (Math.abs(detourTarget.depth - detourSource.depth) > 1 || detourTarget.depth < detourSource.depth),
                );
              }).length;
              const direction = detourIndex % 2 === 0 ? 1 : -1;
              const lane = Math.floor(detourIndex / 2);
              const laneX = direction > 0 ? layout.width - 66 - lane * 38 : 66 + lane * 38;
              const sourceX = source.x + direction * DIAGRAM_NODE_WIDTH / 2;
              const targetX = target.x + direction * DIAGRAM_NODE_WIDTH / 2;
              path = `M ${sourceX} ${source.y} C ${laneX} ${source.y}, ${laneX} ${target.y}, ${targetX} ${target.y}`;
              labelX = laneX;
              labelY = (source.y + target.y) / 2;
            }

            const isRelated = !focusedNode || edge.from === focusedNode || edge.to === focusedNode;
            const isSecondary = secondaryEdges.includes(edge);
            const edgeNumber = edges.indexOf(edge) + 1;

            return (
              <g className={`diagram-edge ${isSecondary ? 'secondary-edge' : ''} ${isRelated ? 'is-related' : 'is-muted'}`} key={`${edge.from}-${edge.to}-${edgeNumber}`}>
                <title>{`${nodeMap.get(edge.from)?.title ?? edge.from} — ${edge.label} → ${nodeMap.get(edge.to)?.title ?? edge.to}`}</title>
                <path d={path} markerEnd={`url(#${markerId})`} />
                <g className="edge-label edge-number" transform={`translate(${labelX}, ${labelY})`}>
                  <circle r="12" />
                  <text textAnchor="middle" dominantBaseline="central">{edgeNumber}</text>
                </g>
              </g>
            );
          })}
        </g>

        <g className="diagram-nodes">
          {layout.nodes.map((node, index) => (
            <g
              className={`diagram-node ${focusedNode === node.id ? 'is-focused' : focusedNode ? 'is-muted' : ''}`}
              transform={`translate(${node.x}, ${node.y})`}
              role="button"
              tabIndex={0}
              aria-pressed={focusedNode === node.id}
              onClick={() => setFocusedNode((value) => value === node.id ? undefined : node.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  setFocusedNode((value) => value === node.id ? undefined : node.id);
                }
              }}
              key={node.id}
            >
              <title>{node.subtitle ? `${node.title}：${node.subtitle}` : node.title}</title>
              <rect x={-DIAGRAM_NODE_WIDTH / 2} y={-DIAGRAM_NODE_HEIGHT / 2} width={DIAGRAM_NODE_WIDTH} height={DIAGRAM_NODE_HEIGHT} rx="13" />
              <circle cx={-DIAGRAM_NODE_WIDTH / 2 + 18} cy={-DIAGRAM_NODE_HEIGHT / 2 + 18} r="10" />
              <text className="node-index" x={-DIAGRAM_NODE_WIDTH / 2 + 18} y={-DIAGRAM_NODE_HEIGHT / 2 + 18} textAnchor="middle" dominantBaseline="central">{index + 1}</text>
              <text className="node-title" x="0" y={node.subtitle ? -11 : 3} textAnchor="middle">{diagramTextLines(node.title, 16, 1)[0]}</text>
              {node.subtitle && (
                <text className="node-subtitle" x="0" y="12" textAnchor="middle">
                  {diagramTextLines(node.subtitle, 22, 2).map((line, lineIndex) => (
                    <tspan x="0" dy={lineIndex === 0 ? 0 : 16} key={`${node.id}-${lineIndex}`}>{line}</tspan>
                  ))}
                </text>
              )}
            </g>
          ))}
        </g>
        </svg>
      </div>
    </div>
  );
}

export default function Home() {
  const [task, setTask] = useState('');
  const [submittedTask, setSubmittedTask] = useState('');
  const [workspace, setWorkspace] = useState('calculator');
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
  const [steeringSubmitting, setSteeringSubmitting] = useState(false);
  const [showInteractionFeedback, setShowInteractionFeedback] = useState(false);
  const [interactionFeedback, setInteractionFeedback] = useState('');
  const [taskRuns, setTaskRuns] = useState<TaskSummary[]>([]);
  const [conversationTurns, setConversationTurns] = useState<ConversationTurn[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [sessionTree, setSessionTree] = useState<SessionTree>();
  const [branchTarget, setBranchTarget] = useState<SessionTreeEntry>();
  const [petOpen, setPetOpen] = useState(false);
  const [petReminder, setPetReminder] = useState('');
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false);
  const [workspaceDraft, setWorkspaceDraft] = useState('calculator');
  const [workspaceListing, setWorkspaceListing] = useState<WorkspaceListing>();
  const [workspaceRootDirectories, setWorkspaceRootDirectories] = useState<WorkspaceDirectory[]>();
  const [workspacePickerError, setWorkspacePickerError] = useState('');
  const [workspacePickerLoading, setWorkspacePickerLoading] = useState(false);
  const [workspaceDeleteTarget, setWorkspaceDeleteTarget] = useState<WorkspaceDirectory>();
  const [workspaceDeleting, setWorkspaceDeleting] = useState(false);
  const [workspaceDeleteError, setWorkspaceDeleteError] = useState('');
  const [workspaceCreateOpen, setWorkspaceCreateOpen] = useState(false);
  const [workspaceNewName, setWorkspaceNewName] = useState('');
  const [workspaceCreating, setWorkspaceCreating] = useState(false);
  const [workspaceCreateError, setWorkspaceCreateError] = useState('');
  const streamRef = useRef<EventSource | null>(null);
  const conversationRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const approvalAllowRef = useRef<HTMLButtonElement | null>(null);
  const interactionApproveRef = useRef<HTMLButtonElement | null>(null);
  const taskStatusesRef = useRef<Map<string, string>>(new Map());
  const taskListReadyRef = useRef(false);
  const reminderTimerRef = useRef<number | undefined>(undefined);
  const availableProjectWorkspaces = workspaceRootDirectories === undefined
    ? PROJECT_WORKSPACES
    : PROJECT_WORKSPACES.filter((project) => workspaceRootDirectories.some((directory) => directory.path === project.path));
  const customProjectWorkspaces = (workspaceRootDirectories ?? [])
    .filter((directory) => !PROJECT_WORKSPACES.some((project) => project.path === directory.path))
    .map((directory) => ({ icon: '⌁', name: directory.name, path: directory.path, stack: '本地项目工作区' }));
  const sidebarProjectWorkspaces = [...availableProjectWorkspaces, ...customProjectWorkspaces];
  const knownActiveWorkspace = sidebarProjectWorkspaces.find((item) => item.path === workspace);
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
        void fetch(`${API_BASE}/api/runs/${id}`)
          .then(async (response) => response.ok ? await response.json() as RunSnapshot : undefined)
          .then((snapshot) => {
            if (!snapshot) return;
            setConversationTurns((snapshot.conversation ?? []).slice(0, -1));
            setSessionId(snapshot.session_id ?? '');
            setSessionTree(snapshot.session_tree ?? undefined);
          })
          .catch(() => undefined);
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
    fetch(`${API_BASE}/api/workspaces?path=.`)
      .then(async (response) => {
        if (!response.ok) throw new Error('workspaces unavailable');
        return await response.json() as WorkspaceListing;
      })
      .then((data) => {
        setWorkspaceRootDirectories(data.directories);
        setWorkspace((current) => {
          const stillExists = current === '.' || data.directories.some(
            (directory) => current === directory.path || current.startsWith(`${directory.path}/`),
          );
          if (stillExists) return current;
          return PROJECT_WORKSPACES.find(
            (project) => data.directories.some((directory) => directory.path === project.path),
          )?.path ?? data.directories[0]?.path ?? '.';
        });
      })
      .catch(() => undefined);
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
    const storedText = sessionStorage.getItem(RUN_SESSION_KEY) ?? sessionStorage.getItem(LEGACY_RUN_SESSION_KEY);
    if (!storedText) return;

    let stored: StoredRun;
    try {
      stored = JSON.parse(storedText) as StoredRun;
      if (!stored.runId || !stored.task || !stored.workspace) throw new Error('invalid run session');
    } catch {
      sessionStorage.removeItem(RUN_SESSION_KEY);
      sessionStorage.removeItem(LEGACY_RUN_SESSION_KEY);
      return;
    }

    let disposed = false;
    fetch(`${API_BASE}/api/runs/${stored.runId}`)
      .then(async (response) => {
        if (!response.ok) {
          if (response.status === 404) {
            sessionStorage.removeItem(RUN_SESSION_KEY);
            sessionStorage.removeItem(LEGACY_RUN_SESSION_KEY);
            return null;
          }
          throw new Error(await response.text());
        }
        return await response.json() as RunSnapshot;
      })
      .then((snapshot) => {
        if (disposed || !snapshot) return;
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
        setConversationTurns((snapshot.conversation ?? []).slice(0, -1));
        setSessionId(snapshot.session_id ?? '');
        setSessionTree(snapshot.session_tree ?? undefined);
        setBranchTarget(undefined);
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
  const activeHookPipeline = activeEvent?.payload?.hook_pipeline as HookPipelineSnapshot | undefined;
  const selectedSkill = [...events].reverse().find((event) => event.type === 'skill_selected');
  const interactionDecision = [...events].reverse().find((event) => event.type === 'interaction_model_decision');
  const interactionFirst = interactionDecision?.payload?.enabled === true
    || events.some((event) => ['interaction_model_created', 'interaction_confirmation_requested'].includes(event.type));
  const visiblePhases = interactionFirst
    ? PHASES
    : PHASES.filter((phase) => !['interaction_modeling', 'interaction_confirmation'].includes(phase.id));
  const currentPhase = [...events].reverse()
    .map((event) => event.phase)
    .find((phase) => visiblePhases.some((item) => item.id === phase)) ?? 'created';
  const currentPhaseIndex = visiblePhases.findIndex((phase) => phase.id === currentPhase);
  const finalEvent = [...events].reverse().find((event) => event.type === 'run_finished');
  const latestTraceabilityEvent = [...events].reverse().find(
    (event) => ['traceability_updated', 'traceability_initialized'].includes(event.type),
  );
  const finalTraceability = finalEvent?.payload?.traceability;
  const traceability = (
    latestTraceabilityEvent?.payload
    ?? (finalTraceability && typeof finalTraceability === 'object' ? finalTraceability : undefined)
  ) as TraceabilitySnapshot | undefined;
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
  const skillTypes = ['skill_candidates', 'skill_confirmation_requested', 'skill_confirmation_resolved', 'skill_selected'];
  const interactionTypes = ['interaction_model_decision', 'interaction_context_collected', 'interaction_model_created', 'interaction_confirmation_requested', 'interaction_confirmation_resolved'];
  const traceabilityTypes = ['traceability_initialized', 'traceability_updated'];
  const steeringTypes = ['steering_received', 'steering_applied', 'steering_deferred_completion'];
  const communicationTypes = [...steeringTypes, 'user_requirement_received'];
  const activityEvents = events.filter((event) => ['run_started', ...skillTypes, 'phase_changed', 'tool_finished', 'file_changed', 'user_guide_ready', ...communicationTypes, 'quality_checkpoint', 'approval_requested', 'approval_resolved', 'error', ...interactionTypes, ...traceabilityTypes].includes(event.type));
  // Communication messages have a dedicated, complete record below the
  // agent introduction; keep the compact execution list focused on actions.
  const chatEvents = events.filter((event) => ['run_started', ...skillTypes, 'tool_finished', 'file_changed', 'user_guide_ready', 'quality_checkpoint', 'approval_requested', 'approval_resolved', 'error', ...interactionTypes, ...traceabilityTypes].includes(event.type));
  const steeringAppliedIds = new Set(
    events
      .filter((event) => event.type === 'steering_applied')
      .map((event) => String(event.payload?.steering_id ?? ''))
      .filter(Boolean),
  );
  const communicationEvents = events.filter((event) => {
    if (communicationTypes.includes(event.type) && event.type !== 'steering_applied') return true;
    // Keep feedback from runs created before the dedicated requirement event
    // was introduced visible when their historical event log is reopened.
    return event.type === 'interaction_confirmation_resolved'
      && event.payload?.decision === 'revise'
      && typeof event.payload?.feedback === 'string'
      && Boolean(event.payload.feedback.trim());
  });

  const tabContent = (() => {
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
  })();

  async function openTask(selectedRun: Pick<TaskSummary, 'run_id'>) {
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
      setConversationTurns((snapshot.conversation ?? []).slice(0, -1));
      setSessionId(snapshot.session_id ?? '');
      setSessionTree(snapshot.session_tree ?? undefined);
      setBranchTarget(undefined);
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
    const parentRunId = branchTarget?.run_id
      ?? (runId && ['completed', 'failed', 'cancelled'].includes(runStatus) ? runId : undefined);
    const parentTurn = !branchTarget && parentRunId && submittedTask ? {
      run_id: parentRunId,
      task: submittedTask,
      status: runStatus,
      summary: finalEvent?.summary ?? '',
      created_at: finalEvent?.timestamp ?? new Date().toISOString(),
    } satisfies ConversationTurn : undefined;
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
        body: JSON.stringify({
          task: submitted,
          workspace: workspace.trim() || '.',
          skill: requestedSkill,
          parent_run_id: parentRunId,
          session_id: sessionId || undefined,
          parent_entry_id: branchTarget?.entry_id,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      const data = await response.json() as { run_id: string; session_id?: string; session_entry_id?: string };
      sessionStorage.setItem(RUN_SESSION_KEY, JSON.stringify({
        runId: data.run_id,
        task: submitted,
        workspace: workspace.trim() || '.',
      } satisfies StoredRun));
      setTask('');
      if (branchTarget && sessionTree) {
        const byId = new Map(sessionTree.entries.map((entry) => [entry.entry_id, entry]));
        const path: SessionTreeEntry[] = [];
        let cursor: SessionTreeEntry | undefined = branchTarget;
        while (cursor) {
          path.push(cursor);
          cursor = cursor.parent_id ? byId.get(cursor.parent_id) : undefined;
        }
        setConversationTurns(path.reverse().map((entry) => ({
          run_id: entry.run_id,
          session_entry_id: entry.entry_id,
          parent_entry_id: entry.parent_id,
          task: entry.task,
          status: entry.status,
          summary: entry.summary,
          created_at: entry.created_at,
        })));
      } else if (parentTurn) setConversationTurns((current) => [...current, parentTurn]);
      setInteractionFeedback('');
      setShowInteractionFeedback(false);
      setSkillChoice('');
      setRunId(data.run_id);
      setSessionId(data.session_id ?? sessionId);
      setBranchTarget(undefined);
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

  async function sendSteering() {
    const message = task.trim();
    if (!runId || runStatus !== 'running' || !message || steeringSubmitting) return;
    setSteeringSubmitting(true);
    setNotice('');
    try {
      const response = await fetch(`${API_BASE}/api/runs/${runId}/steering`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      });
      if (!response.ok) throw new Error(await response.text());
      setTask('');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '无法发送方向修正');
    } finally {
      setSteeringSubmitting(false);
      composerRef.current?.focus();
    }
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

  async function loadWorkspaceDirectory(path: string) {
    setWorkspacePickerLoading(true);
    setWorkspacePickerError('');
    try {
      const response = await fetch(`${API_BASE}/api/workspaces?path=${encodeURIComponent(path.trim() || '.')}`);
      const data = await response.json() as WorkspaceListing & { detail?: string };
      if (!response.ok) throw new Error(data.detail || '无法读取该文件夹');
      setWorkspaceListing(data);
      if (data.current === '.') setWorkspaceRootDirectories(data.directories);
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
    setWorkspaceCreateOpen(false);
    setWorkspaceCreateError('');
    setWorkspaceNewName('');
    setWorkspacePickerOpen(true);
    void loadWorkspaceDirectory(workspace);
  }

  async function createNewWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (workspaceCreating) return;
    const name = workspaceNewName.trim();
    if (!name) {
      setWorkspaceCreateError('请输入新项目的文件夹名称。');
      return;
    }
    if (workspaceIsBusy(name)) {
      setWorkspaceCreateError('该路径与运行中的任务工作区重叠，请稍后再创建。');
      return;
    }

    setWorkspaceCreating(true);
    setWorkspaceCreateError('');
    try {
      const response = await fetch(`${API_BASE}/api/workspaces`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await response.json() as { workspace?: WorkspaceDirectory; detail?: string };
      if (!response.ok || !data.workspace) throw new Error(data.detail || '无法创建新工作区');
      const createdWorkspace = data.workspace;

      let rootData: WorkspaceListing | undefined;
      try {
        const rootResponse = await fetch(`${API_BASE}/api/workspaces?path=.`);
        if (rootResponse.ok) rootData = await rootResponse.json() as WorkspaceListing;
      } catch {
        // The newly created workspace is still usable; keep the local list in sync below.
      }

      const previousTaskContinues = Boolean(runId && isActiveStatus(runStatus));
      const nextDirectories = rootData?.directories
        ?? [...(workspaceRootDirectories ?? []).filter((directory) => directory.path !== createdWorkspace.path), createdWorkspace];
      setWorkspaceRootDirectories(nextDirectories);
      if (rootData) setWorkspaceListing(rootData);
      setWorkspaceDraft(createdWorkspace.path);
      setWorkspaceNewName('');
      setWorkspaceCreateOpen(false);
      setWorkspacePickerOpen(false);
      clearCurrentRunForWorkspace(createdWorkspace.path);
      setNotice(
        previousTaskContinues
          ? `原任务会继续在后台运行，已创建并选择 ${createdWorkspace.name} 工作区。`
          : `已创建空工作区 ${createdWorkspace.name}，请描述要实现的新项目。`,
      );
      window.setTimeout(() => composerRef.current?.focus(), 0);
    } catch (error) {
      setWorkspaceCreateError(error instanceof Error ? error.message : '无法创建新工作区');
    } finally {
      setWorkspaceCreating(false);
    }
  }

  async function deleteWorkspace() {
    if (!workspaceDeleteTarget || workspaceDeleting) return;

    const deletedWorkspace = workspaceDeleteTarget.path;
    setWorkspaceDeleting(true);
    setWorkspaceDeleteError('');
    setWorkspacePickerError('');
    try {
      const response = await fetch(`${API_BASE}/api/workspaces?path=${encodeURIComponent(deletedWorkspace)}`, { method: 'DELETE' });
      const data = await response.json() as { detail?: string; trash_location?: string };
      if (!response.ok) throw new Error(data.detail || '无法删除该工作区');

      const rootResponse = await fetch(`${API_BASE}/api/workspaces?path=.`);
      const rootData = await rootResponse.json() as WorkspaceListing & { detail?: string };
      if (!rootResponse.ok) throw new Error(rootData.detail || '工作区已删除，但列表刷新失败');

      setWorkspaceDeleteTarget(undefined);
      setWorkspaceDeleteError('');
      setWorkspaceListing(rootData);
      setWorkspaceRootDirectories(rootData.directories);
      setWorkspaceDraft('.');

      if (workspacePathsOverlap(workspace, deletedWorkspace)) {
        const nextWorkspace = PROJECT_WORKSPACES.find(
          (project) => rootData.directories.some((directory) => directory.path === project.path),
        )?.path ?? rootData.directories[0]?.path ?? '.';
        clearCurrentRunForWorkspace(nextWorkspace);
      }
      setNotice(`工作区 ${deletedWorkspace} 已移到本地回收区，需要时可以恢复。`);
    } catch (error) {
      const message = error instanceof Error ? error.message : '无法删除该工作区';
      setWorkspaceDeleteError(message);
      setWorkspacePickerError(message);
    } finally {
      setWorkspaceDeleting(false);
    }
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

      setWorkspacePickerOpen(false);
      const latest = taskRuns.find((run) => run.workspace === data.current);
      if (latest) {
        await openTask(latest);
        setNotice(
          previousTaskContinues
            ? `原任务会继续在后台运行，已恢复 ${selectedName} 工作区最近的对话。`
            : `已恢复 ${selectedName} 工作区最近的对话，可以继续补充要求。`,
        );
      } else {
        clearCurrentRunForWorkspace(data.current);
        setNotice(
          previousTaskContinues
            ? `原任务会继续在后台运行，已为新任务选择 ${selectedName} 工作区。`
            : `已选择 ${selectedName} 工作区，请输入新任务需求。`,
        );
      }
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
    setConversationTurns([]);
    setSessionId('');
    setSessionTree(undefined);
    setBranchTarget(undefined);
    setPetOpen(false);
  }

  function selectWorkspace(nextWorkspace: string) {
    if (nextWorkspace === workspace && submittedTask) return;
    const latest = taskRuns.find((run) => run.workspace === nextWorkspace);
    if (latest) {
      void openTask(latest);
      return;
    }
    clearCurrentRunForWorkspace(nextWorkspace);
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (runStatus === 'running') void sendSteering();
      else void startRun();
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="brand-lockup">
            <span className="brand-glyph" aria-hidden="true">›_</span>
            <strong>IntentFlow</strong>
          </div>
          <button className="new-run-button" type="button" onClick={newRun}>
            <span aria-hidden="true">＋</span> 新建任务
          </button>
        </div>

        <nav className="sidebar-nav" aria-label="任务导航">
          <p>工作区</p>
          {sidebarProjectWorkspaces.map((project) => {
            const busy = workspaceIsBusy(project.path);
            return (
              <div className="nav-workspace-row" key={project.path}>
                <button
                  className={`nav-item ${workspace === project.path ? 'active' : ''}`}
                  type="button"
                  onClick={() => selectWorkspace(project.path)}
                  aria-pressed={workspace === project.path}
                >
                  <span className="nav-icon">{project.icon}</span>
                  <span><strong>{project.name}</strong><small>{project.stack}</small></span>
                </button>
                <button
                  className="nav-workspace-delete"
                  type="button"
                  onClick={() => { setWorkspaceDeleteError(''); setWorkspaceDeleteTarget({ name: project.name, path: project.path }); }}
                  disabled={busy}
                  aria-label={`删除工作区 ${project.name}`}
                  title={busy ? '任务运行中，暂时不能删除' : `删除 ${project.name}`}
                >删除</button>
              </div>
            );
          })}
          {!knownActiveWorkspace && (
            <div className="nav-workspace-row">
              <button className="nav-item active" type="button" aria-pressed="true">
                <span className="nav-icon">⌁</span>
                <span><strong>{activeWorkspace.name}</strong><small>{workspace}</small></span>
              </button>
              {workspace !== '.' && workspace.split('/').filter(Boolean).length === 1 && (
                <button
                  className="nav-workspace-delete"
                  type="button"
                  onClick={() => { setWorkspaceDeleteError(''); setWorkspaceDeleteTarget({ name: activeWorkspace.name, path: workspace }); }}
                  disabled={workspaceIsBusy(workspace)}
                  aria-label={`删除工作区 ${activeWorkspace.name}`}
                  title={workspaceIsBusy(workspace) ? '任务运行中，暂时不能删除' : `删除 ${activeWorkspace.name}`}
                >删除</button>
              )}
            </div>
          )}

          <p className="sidebar-section-label">Agent 设置</p>
          <a className="nav-item" href="/skills">
            <span className="nav-icon">S</span>
            <span>
              <strong>Skill 管理</strong>
              <small>添加、启用或停用能力</small>
            </span>
          </a>
          <a className="nav-item" href="/settings">
            <span className="nav-icon">⚙</span>
            <span>
              <strong>Agent 设置</strong>
              <small>模式、工作流与执行预算</small>
            </span>
          </a>

        </nav>

        <div className="pet-companion">
          <button type="button" className={`pet-companion-button ${petMood}`} onClick={() => setPetOpen((open) => !open)} aria-expanded={petOpen} aria-controls="flowpet-task-center">
            <span className="pet-avatar" aria-hidden="true">ʕ•ᴥ•ʔ<i /></span>
            <span className="pet-companion-copy"><strong>FlowPet</strong><small>{petMessage}</small></span>
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
          <button className="workspace-picker-backdrop" type="button" onClick={() => { setWorkspacePickerOpen(false); setWorkspaceDeleteTarget(undefined); setWorkspaceCreateOpen(false); }} aria-label="关闭工作区选择器" />
          <section className="workspace-picker" role="dialog" aria-modal="true" aria-labelledby="workspace-picker-title">
            <header>
              <div><span aria-hidden="true">⌁</span><div><small>NEW TASK</small><h2 id="workspace-picker-title">选择本地工作区</h2></div></div>
              <button type="button" onClick={() => { setWorkspacePickerOpen(false); setWorkspaceDeleteTarget(undefined); setWorkspaceCreateOpen(false); }} aria-label="关闭">×</button>
            </header>

            <div className="workspace-picker-body">
              <aside>
                <small>预置工作区</small>
                <div className="workspace-preset-list">
                  {availableProjectWorkspaces.map((project) => {
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
                  <div><input id="workspace-path" value={workspaceDraft} onChange={(event) => setWorkspaceDraft(event.target.value)} placeholder="例如 my-project" autoComplete="off" /><button type="submit" disabled={workspacePickerLoading}>定位</button></div>
                </form>

                {workspacePickerError && <div className="workspace-picker-error" role="alert"><span>!</span>{workspacePickerError}</div>}

                <div className="workspace-directory-head">
                  <div><strong>{workspaceListing?.current === '.' ? '项目工作区 · 可管理' : '子文件夹'}</strong><small>{workspaceListing?.current ?? '.'}</small></div>
                  <button type="button" onClick={() => { setWorkspaceNewName(''); setWorkspaceCreateError(''); setWorkspaceCreateOpen(true); }} disabled={workspacePickerLoading}>
                    <span>＋</span> 新建项目工作区
                  </button>
                </div>
                <div className="workspace-directory-list" aria-busy={workspacePickerLoading}>
                  {workspaceListing?.parent !== null && workspaceListing && (
                    <button className="workspace-directory-open standalone" type="button" onClick={() => void loadWorkspaceDirectory(workspaceListing.parent ?? '.')} disabled={workspacePickerLoading}>
                      <span className="folder-icon">↰</span><span><strong>返回上一级</strong><small>{workspaceListing.parent ?? '.'}</small></span>
                    </button>
                  )}
                  {workspaceListing?.directories.map((directory) => {
                    const busy = workspaceIsBusy(directory.path);
                    return (
                      <div className="workspace-directory-row" key={directory.path}>
                        <button className="workspace-directory-open" type="button" onClick={() => void loadWorkspaceDirectory(directory.path)} disabled={workspacePickerLoading}>
                          <span className="folder-icon">▰</span><span><strong>{directory.name}</strong><small>{directory.path}</small></span>{busy && <em>任务运行中</em>}
                        </button>
                        {workspaceListing.current === '.' && (
                          <button
                            className="workspace-directory-delete"
                            type="button"
                            onClick={() => { setWorkspaceDeleteError(''); setWorkspaceDeleteTarget(directory); }}
                            disabled={workspacePickerLoading || busy}
                            aria-label={`删除工作区 ${directory.name}`}
                            title={busy ? '任务运行中，暂时不能删除' : `删除 ${directory.name}`}
                          >删除</button>
                        )}
                      </div>
                    );
                  })}
                  {!workspacePickerLoading && workspaceListing?.directories.length === 0 && <div className="workspace-directory-empty">这个文件夹中没有可继续浏览的子目录</div>}
                  {workspacePickerLoading && <div className="workspace-directory-empty">正在读取本地文件夹…</div>}
                </div>
              </div>
            </div>

            <footer>
              <div><small>当前选择</small><code>{workspaceDraft || '.'}</code></div>
              <button type="button" onClick={() => { setWorkspacePickerOpen(false); setWorkspaceDeleteTarget(undefined); setWorkspaceCreateOpen(false); }}>取消</button>
              <button type="button" className="confirm-workspace" onClick={() => void confirmNewWorkspace()} disabled={workspacePickerLoading || workspaceIsBusy(workspaceDraft.trim() || '.')}>
                选择此工作区
              </button>
            </footer>
          </section>
        </>
      )}

      {workspaceCreateOpen && (
        <>
          <button className="workspace-create-backdrop" type="button" onClick={() => { if (!workspaceCreating) { setWorkspaceCreateOpen(false); setWorkspaceCreateError(''); } }} aria-label="取消新建工作区" />
          <section className="workspace-create-dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-create-title" aria-describedby="workspace-create-description">
            <header>
              <span aria-hidden="true">＋</span>
              <div><small>NEW PROJECT WORKSPACE</small><h2 id="workspace-create-title">新建项目工作区</h2></div>
            </header>
            <form onSubmit={createNewWorkspace}>
              <p id="workspace-create-description">创建一个只属于新项目的空文件夹。创建后会立即选中它，你可以直接向 Agent 描述要实现的项目。</p>
              <label htmlFor="workspace-new-name">文件夹名称</label>
              <input
                id="workspace-new-name"
                value={workspaceNewName}
                onChange={(event) => { setWorkspaceNewName(event.target.value); setWorkspaceCreateError(''); }}
                placeholder="例如 pomodoro-app"
                maxLength={80}
                autoComplete="off"
                autoFocus
                disabled={workspaceCreating}
              />
              <div className="workspace-create-location"><span>创建位置</span><code>{workspaceListing?.root_path ?? 'workspaces'}/{workspaceNewName.trim() || '新项目'}</code></div>
              {workspaceCreateError && <p className="workspace-create-error" role="alert">{workspaceCreateError}</p>}
              <footer>
                <button type="button" onClick={() => { setWorkspaceCreateOpen(false); setWorkspaceCreateError(''); }} disabled={workspaceCreating}>取消</button>
                <button className="confirm-create-workspace" type="submit" disabled={workspaceCreating || !workspaceNewName.trim()}>
                  {workspaceCreating ? '正在创建…' : '创建并开始项目'}
                </button>
              </footer>
            </form>
          </section>
        </>
      )}

      {workspaceDeleteTarget && (
        <>
          <button className="workspace-delete-backdrop" type="button" onClick={() => { if (!workspaceDeleting) { setWorkspaceDeleteTarget(undefined); setWorkspaceDeleteError(''); } }} aria-label="取消删除工作区" />
          <section className="workspace-delete-dialog" role="alertdialog" aria-modal="true" aria-labelledby="workspace-delete-title" aria-describedby="workspace-delete-description">
            <span className="workspace-delete-icon" aria-hidden="true">!</span>
            <div>
              <small>DELETE WORKSPACE</small>
              <h2 id="workspace-delete-title">移除“{workspaceDeleteTarget.name}”工作区？</h2>
              <p id="workspace-delete-description">项目将从工作区列表中移除，并完整转移到本地回收区，不会立即永久删除。</p>
              <code>{workspaceDeleteTarget.path}</code>
              {workspaceDeleteError && <p className="workspace-delete-error" role="alert">{workspaceDeleteError}</p>}
            </div>
            <footer>
              <button type="button" onClick={() => { setWorkspaceDeleteTarget(undefined); setWorkspaceDeleteError(''); }} disabled={workspaceDeleting}>取消</button>
              <button className="confirm-delete-workspace" type="button" onClick={() => void deleteWorkspace()} disabled={workspaceDeleting}>
                {workspaceDeleting ? '正在移动…' : '移到回收区'}
              </button>
            </footer>
          </section>
        </>
      )}

      <button type="button" className={`pet-mobile-launch ${petMood}`} onClick={() => setPetOpen(true)} aria-label={`打开 FlowPet，${petMessage}`}>
        <span aria-hidden="true">ʕ•ᴥ•ʔ</span><i>{activeTaskCount}</i>
      </button>

      {petOpen && (
        <>
          <button className="pet-panel-backdrop" type="button" onClick={() => setPetOpen(false)} aria-label="关闭任务中心" />
          <aside className="pet-task-center" id="flowpet-task-center" role="dialog" aria-modal="false" aria-labelledby="flowpet-title">
            <header>
              <div><span className={`pet-panel-avatar ${petMood}`} aria-hidden="true">ʕ•ᴥ•ʔ</span><div><small>YOUR TASK COMPANION</small><h2 id="flowpet-title">FlowPet 任务中心</h2></div></div>
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
          <div><strong>FlowPet 提醒</strong><p>{petReminder}</p></div>
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
              {conversationTurns.map((turn) => {
                const treeEntry = sessionTree?.entries.find((entry) => entry.run_id === turn.run_id);
                return (
                <div className="conversation-history-turn" key={turn.run_id}>
                  <section className="message user-message">
                    <div className="message-avatar user-avatar">你</div>
                    <div className="message-body">
                      <div className="message-meta"><strong>你</strong><time>{formatTime(turn.created_at)}</time></div>
                      <p>{turn.task}</p>
                    </div>
                  </section>
                  <section className="message agent-message history-agent-message">
                    <div className="message-avatar agent-avatar">›_</div>
                    <div className="message-body">
                      <div className="message-meta"><strong>IntentFlow</strong><span className="agent-badge">HISTORY</span></div>
                      <p>{turn.summary || (turn.status === 'completed' ? '该轮任务已完成。' : '该轮任务未完成，可以继续处理。')}</p>
                    </div>
                  </section>
                  {treeEntry && ['completed', 'failed', 'cancelled'].includes(treeEntry.status) && (
                    <div className="history-branch-row">
                      <button type="button" onClick={() => { setBranchTarget(treeEntry); composerRef.current?.focus(); }}>
                        ↳ 从这里创建分支
                      </button>
                    </div>
                  )}
                </div>
              );})}

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
                  <div className="message-meta"><strong>IntentFlow</strong><span className="agent-badge">AGENT</span></div>
                  <p className="agent-intro">
                    {interactionFirst && !selectedSkill
                      ? '独立需求分析器判断本任务需要先确认终端用户流程；确认后再自动组合合适的 Skill。'
                      : selectedSkill
                        ? `已组合 ${selectedSkill.title}。我会综合这些策略理解项目、实施修改并运行验证。`
                        : '正在分析任务并组合合适的 Skill…'}
                  </p>

                  {communicationEvents.length > 0 && (
                    <section className="communication-log" aria-label="用户补充与方向修正记录">
                      <div className="communication-log-head">
                        <strong>沟通记录</strong>
                        <small>{communicationEvents.length} 条</small>
                      </div>
                      <div className="communication-log-list">
                        {communicationEvents.map((event) => {
                          const message = typeof event.payload?.message === 'string'
                            ? event.payload.message
                            : typeof event.payload?.feedback === 'string'
                              ? event.payload.feedback
                              : event.summary;
                          const steeringId = String(event.payload?.steering_id ?? '');
                          const applied = event.type === 'steering_received' && steeringAppliedIds.has(steeringId);
                          const isRequirement = event.type === 'user_requirement_received' || event.type === 'interaction_confirmation_resolved';
                          return (
                            <div className={`communication-item ${isRequirement ? 'requirement' : 'steering'}`} key={event.event_id}>
                              <span className="communication-icon">{isRequirement ? '✦' : '↗'}</span>
                              <div className="communication-copy">
                                <div><strong>{isRequirement ? '补充需求' : 'Steering 方向修正'}</strong><time>{formatTime(event.timestamp)}</time></div>
                                <p>{message}</p>
                              </div>
                              <em>{isRequirement ? '已用于更新流程' : applied ? '已生效' : '等待处理'}</em>
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  )}

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
                        <div className="flowchart-legend"><span><i className="node-sample" />页面或弹层</span><span><i className="edge-sample" />主流程方向</span><span><i className="feedback-sample" />反馈回路（可展开）</span></div>
                        <div className="flow-detail-head"><strong>路径说明</strong><small>编号与图中一致，完整保留用户操作</small></div>
                        <ol className={`flow-edge-list ${(interactionModel.flows?.length ?? 0) > 6 ? 'complex-flow-list' : ''}`}>
                          {(interactionModel.flows ?? []).map((flow, index) => {
                            const source = interactionModel.pages?.find((page) => page.id === flow.from)?.name ?? flow.from;
                            const target = interactionModel.pages?.find((page) => page.id === flow.to)?.name ?? flow.to;
                            return (
                              <li key={`${flow.from}-${flow.to}-${index}`}>
                                <span>{index + 1}</span>
                                <strong>{source}</strong>
                                <em>{flow.action}</em>
                                <b aria-hidden="true">→</b>
                                <strong>{target}</strong>
                              </li>
                            );
                          })}
                        </ol>
                      </div>

                      <div className="interaction-columns">
                        <div className="interaction-section criteria-section">
                          <div className="interaction-section-title"><strong>验收标准</strong><small>{interactionModel.acceptance_criteria?.length ?? 0} 项</small></div>
                          <ul className="criteria-list">
                            {(interactionModel.acceptance_criteria ?? []).map((criterion, index) => {
                              const criterionId = typeof criterion === 'string'
                                ? `AC-${String(index + 1).padStart(2, '0')}`
                                : criterion.id;
                              const description = typeof criterion === 'string' ? criterion : criterion.description;
                              return (
                                <li key={criterionId}>
                                  <span>{criterionId}</span>
                                  {description}
                                </li>
                              );
                            })}
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
                          {traceability?.active && <span>AC {traceability.verified}/{traceability.total}</span>}
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
              {branchTarget && (
                <div className="branch-composer-banner">
                  <span>↳</span>
                  <p>将从“{branchTarget.task.slice(0, 36)}”创建新分支</p>
                  <button type="button" onClick={() => setBranchTarget(undefined)} aria-label="取消分支">×</button>
                </div>
              )}
              <textarea
                ref={composerRef}
                value={task}
                onChange={(event) => setTask(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                rows={3}
                aria-label="给 IntentFlow 的任务"
                placeholder={runStatus === 'running'
                  ? '输入运行中的方向修正，例如：不要修改后端，优先完善移动端交互…'
                  : `描述要在 ${activeWorkspace.name} 工作区完成的任务…`}
              />
              <div className="composer-toolbar">
                <div className="path-pill" title={workspace}>
                  <span>⌁</span>
                  <code>{workspace}</code>
                </div>
                {runStatus === 'running' ? (
                  <span className="steering-mode-pill" title="消息会在下一次模型决策前生效">↗ Steering</span>
                ) : (
                  <label className="composer-skill-picker" title="自动组合最多三项互补 Skill，或手动锁定一项">
                    <span>S</span>
                    <select value={requestedSkill} onChange={(event) => setRequestedSkill(event.target.value)} aria-label="任务 Skill">
                      <option value="auto">自动组合 Skill（最多 3 项）</option>
                      {skillOptions.map((skill) => (
                        <option value={skill.name} key={skill.name}>{skill.display_name}</option>
                      ))}
                    </select>
                  </label>
                )}
                <span className="keyboard-hint">{runStatus === 'running' ? 'Enter 修正方向 · Shift + Enter 换行' : 'Enter 发送 · Shift + Enter 换行'}</span>
                {isActiveStatus(runStatus) ? (
                  <>
                    <button className="steering-send-button" type="button" onClick={() => void sendSteering()} disabled={!task.trim() || steeringSubmitting || runStatus !== 'running'} aria-label="发送方向修正">
                      {steeringSubmitting ? '…' : '↗'}
                    </button>
                    <button className="stop-button" type="button" onClick={cancelRun} aria-label="停止任务">■</button>
                  </>
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
                : runStatus === 'running'
                  ? '可以随时补充方向；消息会在下一次模型决策前生效，不会跳过授权和质量检查。'
                : 'IntentFlow 会在本地受控工作区中读写文件并执行命令，请审查重要改动。'}
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

          {traceability?.active && (
            <section className="inspector-section traceability-section">
              <div className="section-title"><span>需求证据</span><small>{traceability.verified}/{traceability.total}</small></div>
              <div className="traceability-progress">
                <span style={{ width: `${traceability.coverage_percent}%` }} />
              </div>
              <div className="traceability-summary">
                <strong>{traceability.coverage_percent}%</strong>
                <span>确认需求已形成实现与验证证据</span>
              </div>
              <div className="requirement-ledger">
                {traceability.requirements.map((requirement) => (
                  <details className={requirement.status} key={requirement.requirement_id}>
                    <summary>
                      <span>{requirement.status === 'verified' ? '✓' : requirement.status === 'failed' ? '!' : requirement.status === 'implemented' ? '◐' : '○'}</span>
                      <p><strong>{requirement.requirement_id}</strong>{requirement.description}</p>
                      <em>{requirement.status === 'verified' ? '已验证' : requirement.status === 'implemented' ? '待验证' : requirement.status === 'failed' ? '验证失败' : '待实现'}</em>
                    </summary>
                    <div className="requirement-evidence-list">
                      {requirement.evidence.length === 0 ? (
                        <p>还没有关联证据。</p>
                      ) : requirement.evidence.slice(-5).map((evidence) => (
                        <div className={evidence.passed ? 'passed' : 'failed'} key={evidence.evidence_id}>
                          <span>{evidence.evidence_type === 'implementation' ? '实现' : evidence.evidence_type === 'verification' ? '验证' : '审查'}</span>
                          <p><strong>{evidence.artifact || evidence.command || evidence.tool}</strong><small>{evidence.summary}</small></p>
                        </div>
                      ))}
                    </div>
                  </details>
                ))}
              </div>
            </section>
          )}

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
              <div><small>ACTIVE SKILLS</small><strong>{selectedSkill.title}</strong><p>{selectedSkill.summary}</p></div>
            </section>
          )}

          {sessionTree && sessionTree.entries.length > 0 && (
            <section className="inspector-section session-tree-section">
              <div className="section-title"><span>Session 分支</span><small>{sessionTree.entries.length} 个节点</small></div>
              <div className="session-tree-list">
                {sessionTree.entries.map((entry) => (
                  <div className={`${entry.active ? 'active' : ''} ${entry.status}`} style={{ paddingLeft: `${entry.depth * 14}px` }} key={entry.entry_id}>
                    <button type="button" className="session-node-main" onClick={() => void openTask({ run_id: entry.run_id })} title={entry.task}>
                      <span>{entry.child_count ? '◆' : '◇'}</span>
                      <p><strong>{entry.task}</strong><small>{taskStatusLabel(entry.status)}</small></p>
                    </button>
                    {['completed', 'failed', 'cancelled'].includes(entry.status) && (
                      <button type="button" className="session-node-branch" onClick={() => { setBranchTarget(entry); composerRef.current?.focus(); }} title="从此节点创建新分支">↳</button>
                    )}
                  </div>
                ))}
              </div>
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
            {tab === 'detail' && activeHookPipeline && (
              <div className="hook-pipeline-strip" aria-label="本次工具调用 Hook 管线">
                <span>BEFORE</span>
                <strong>{hookLabel(String(activeHookPipeline.before?.hook ?? 'hook_pipeline'))}</strong>
                <i>→</i>
                <span>TOOL</span>
                <i>→</i>
                <span>AFTER</span>
                <strong>{(activeHookPipeline.after ?? []).map(hookLabel).join(' · ') || '无后置处理'}</strong>
              </div>
            )}
            <pre className={`code-view ${tab}`}>{tabContent}</pre>
          </section>
        </div>

        <footer className="guard-footer"><span>◇</span><p><strong>Workspace Guard</strong><small>路径与命令均在本地校验</small></p></footer>
      </aside>
    </main>
  );
}
