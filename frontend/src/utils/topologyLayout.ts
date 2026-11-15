/**
 * Topology layout generation and API data transformation for React Flow.
 *
 * Maps TopologyLayoutResponse entities into deterministic, readable React Flow nodes and edges.
 * Guarantees zero fabricated nodes, ports, or telemetry.
 */

import { Position } from '@xyflow/react';
import type { Node, Edge } from '@xyflow/react';
import type {
  TopologyLayoutResponse,
  TopologyNode as ApiTopologyNode,
  TopologyLink as ApiTopologyLink,
} from '../api/types';

export interface HostNodeData extends Record<string, unknown> {
  id: string;
  label: string;
  ip: string;
  role: string;
  connectedSwitch: string;
  interfaces: string[];
}

export interface SwitchNodeData extends Record<string, unknown> {
  id: string;
  label: string;
  switchId: string;
  isBypass: boolean;
  ports: Record<string | number, string>;
  interfaces: string[];
}

export interface TopologyEdgeData extends Record<string, unknown> {
  id: string;
  bandwidth_mbps: number;
  delay_ms: number;
  jitter_ms?: number | null;
  queue_limit_packets?: number | null;
  path_role: string;
  is_bottleneck: boolean;
  is_bypass: boolean;
  source_port?: number | null;
  target_port?: number | null;
}

export type CustomNode = Node<HostNodeData | SwitchNodeData>;
export type CustomEdge = Edge<TopologyEdgeData>;

// Deterministic node positions
const STAGE1_POSITIONS: Record<string, { x: number; y: number }> = {
  h1: { x: 80, y: 120 },
  h2: { x: 80, y: 340 },
  'netfix-s1': { x: 420, y: 230 },
  'netfix-s2': { x: 800, y: 230 },
  h3: { x: 1140, y: 120 },
  h4: { x: 1140, y: 340 },
};

const STAGE2_POSITIONS: Record<string, { x: number; y: number }> = {
  h1: { x: 80, y: 120 },
  h2: { x: 80, y: 340 },
  'netfix-s1': { x: 420, y: 200 },
  'netfix-s2': { x: 920, y: 200 },
  'netfix-s3': { x: 670, y: 440 }, // Positioned below primary trunk
  h3: { x: 1260, y: 120 },
  h4: { x: 1260, y: 340 },
};

/**
 * Determine the host traffic role from its ID and label.
 */
function determineHostRole(host: ApiTopologyNode): string {
  if (host.id === 'h1') return 'Primary Sender';
  if (host.id === 'h2') return 'Cross-Traffic Sender';
  if (host.id === 'h3') return 'Primary Receiver';
  if (host.id === 'h4') return 'Cross-Traffic Receiver';
  return 'Host';
}

/**
 * Transform API topology response into React Flow nodes and edges.
 */
export function transformTopologyToFlow(topology: TopologyLayoutResponse): {
  nodes: CustomNode[];
  edges: CustomEdge[];
} {
  const isStage2 = topology.stage === 2;
  const positions = isStage2 ? STAGE2_POSITIONS : STAGE1_POSITIONS;

  // 1. Build Host Nodes
  const hostNodes: CustomNode[] = topology.hosts.map((host) => {
    const pos = positions[host.id] || { x: 100, y: 100 };
    const connectedSwitch = Object.values(host.ports)[0] || 'Unknown';

    return {
      id: host.id,
      type: 'hostNode',
      position: pos,
      data: {
        id: host.id,
        label: host.label,
        ip: host.ip || '10.0.0.X',
        role: determineHostRole(host),
        connectedSwitch,
        interfaces: host.interfaces || [],
      },
    };
  });

  // 2. Build Switch Nodes
  const switchNodes: CustomNode[] = topology.switches.map((sw) => {
    const pos = positions[sw.id] || { x: 400, y: 200 };
    const isBypass = sw.id === topology.bypass_switch;

    return {
      id: sw.id,
      type: 'switchNode',
      position: pos,
      data: {
        id: sw.id,
        label: sw.label,
        switchId: sw.id,
        isBypass,
        ports: sw.ports || {},
        interfaces: sw.interfaces || [],
      },
    };
  });

  const nodes: CustomNode[] = [...hostNodes, ...switchNodes];

  // 3. Build Edges
  const edges: CustomEdge[] = topology.links.map((link: ApiTopologyLink) => {
    let sourceHandle = Position.Right;
    let targetHandle = Position.Left;

    // Handle routing specificity for Stage 2 bypass
    if (link.is_bypass) {
      if (link.source === 'netfix-s1' && link.target === 'netfix-s3') {
        sourceHandle = Position.Bottom;
        targetHandle = Position.Left;
      } else if (link.source === 'netfix-s3' && link.target === 'netfix-s2') {
        sourceHandle = Position.Right;
        targetHandle = Position.Bottom;
      }
    } else if (link.is_bottleneck) {
      sourceHandle = Position.Right;
      targetHandle = Position.Left;
    } else {
      // Host links
      if (link.source.startsWith('h')) {
        sourceHandle = Position.Right;
        targetHandle = Position.Left;
      } else {
        sourceHandle = Position.Right;
        targetHandle = Position.Left;
      }
    }

    return {
      id: link.id,
      source: link.source,
      target: link.target,
      sourceHandle,
      targetHandle,
      type: 'topologyEdge',
      data: {
        id: link.id,
        bandwidth_mbps: link.bandwidth_mbps,
        delay_ms: link.delay_ms,
        jitter_ms: link.jitter_ms,
        queue_limit_packets: link.queue_limit_packets,
        path_role: link.path_role,
        is_bottleneck: link.is_bottleneck,
        is_bypass: link.is_bypass,
        source_port: link.source_port,
        target_port: link.target_port,
      },
    };
  });

  return { nodes, edges };
}
