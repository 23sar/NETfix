import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  useNodesState,
  useEdgesState,
  useReactFlow,
} from '@xyflow/react';
import type { Node, Edge, NodeMouseHandler, EdgeMouseHandler } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useTopology } from '../api/hooks/useTopology';
import {
  transformTopologyToFlow,
  type HostNodeData,
  type SwitchNodeData,
  type TopologyEdgeData,
} from '../utils/topologyLayout';

import { HostNode } from '../components/topology/HostNode';
import { SwitchNode } from '../components/topology/SwitchNode';
import { TopologyEdge } from '../components/topology/TopologyEdge';
import { TopologyToolbar } from '../components/topology/TopologyToolbar';
import { TopologyLegend } from '../components/topology/TopologyLegend';
import { NodeDetailPanel } from '../components/topology/NodeDetailPanel';
import { LinkDetailPanel } from '../components/topology/LinkDetailPanel';
import { LoadingState } from '../components/common/LoadingState';
import { ErrorState } from '../components/common/ErrorState';

const nodeTypes = {
  hostNode: HostNode,
  switchNode: SwitchNode,
};

const edgeTypes = {
  topologyEdge: TopologyEdge,
};

const TopologyCanvas: React.FC<{
  stage: number;
  onSelectStage: (stage: number) => void;
}> = ({ stage, onSelectStage }) => {
  const { data: topology, isLoading, isError, error, refetch } = useTopology(stage);
  const reactFlow = useReactFlow();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  // Selection states for detail panels
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);

  // Sync API data to React Flow nodes/edges
  useEffect(() => {
    if (topology) {
      const { nodes: flowNodes, edges: flowEdges } = transformTopologyToFlow(topology);
      setNodes(flowNodes);
      setEdges(flowEdges);
      setSelectedNode(null);
      setSelectedEdge(null);

      // Auto fit view after layout transformation
      setTimeout(() => {
        reactFlow.fitView({ padding: 0.2, duration: 400 });
      }, 50);
    }
  }, [topology, setNodes, setEdges, reactFlow]);

  // Handle node selection
  const handleNodeClick: NodeMouseHandler = useCallback((_, node) => {
    setSelectedNode(node);
    setSelectedEdge(null);
  }, []);

  // Handle edge selection
  const handleEdgeClick: EdgeMouseHandler = useCallback((_, edge) => {
    setSelectedEdge(edge);
    setSelectedNode(null);
  }, []);

  // Clear selection on background canvas click
  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
  }, []);

  // Toolbar actions
  const handleFitView = useCallback(() => {
    reactFlow.fitView({ padding: 0.2, duration: 300 });
  }, [reactFlow]);

  const handleZoomIn = useCallback(() => {
    reactFlow.zoomIn({ duration: 200 });
  }, [reactFlow]);

  const handleZoomOut = useCallback(() => {
    reactFlow.zoomOut({ duration: 200 });
  }, [reactFlow]);

  const handleResetLayout = useCallback(() => {
    if (topology) {
      const { nodes: flowNodes, edges: flowEdges } = transformTopologyToFlow(topology);
      setNodes(flowNodes);
      setEdges(flowEdges);
      setTimeout(() => {
        reactFlow.fitView({ padding: 0.2, duration: 300 });
      }, 50);
    }
  }, [topology, setNodes, setEdges, reactFlow]);

  // Memoized node data for inspection panel
  const selectedNodeData = useMemo(() => {
    if (!selectedNode) return null;
    return selectedNode.data as unknown as HostNodeData | SwitchNodeData;
  }, [selectedNode]);

  const selectedNodeType = useMemo(() => {
    if (!selectedNode) return null;
    return selectedNode.type as 'hostNode' | 'switchNode';
  }, [selectedNode]);

  // Memoized edge data for inspection panel
  const selectedEdgeData = useMemo(() => {
    if (!selectedEdge) return null;
    return selectedEdge.data as unknown as TopologyEdgeData;
  }, [selectedEdge]);

  if (isLoading && (!nodes || nodes.length === 0)) {
    return <LoadingState message="Loading Mininet virtual network schematic from API..." />;
  }

  if (isError) {
    return (
      <ErrorState
        title="Failed to Load Topology Layout"
        message={error instanceof Error ? error.message : 'Backend unreachable'}
        onRetry={() => refetch()}
      />
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8.5rem)] min-h-[600px] space-y-3">
      {/* Top Controls Toolbar */}
      <TopologyToolbar
        currentStage={stage}
        onSelectStage={onSelectStage}
        onFitView={handleFitView}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        onResetLayout={handleResetLayout}
        nodeCount={nodes.length}
        edgeCount={edges.length}
        isLoading={isLoading}
      />

      {/* Main Canvas + Side Inspection Panel */}
      <div className="flex-1 relative bg-noc-950 border border-noc-800 rounded-lg overflow-hidden flex">
        {/* React Flow Workspace */}
        <div className="flex-1 h-full relative">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={handleNodeClick}
            onEdgeClick={handleEdgeClick}
            onPaneClick={handlePaneClick}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            minZoom={0.3}
            maxZoom={2.0}
            fitView
            proOptions={{ hideAttribution: true }}
            className="bg-noc-950 select-none"
          >
            {/* Dark NOC Grid Background */}
            <Background color="#1E293B" gap={24} size={1.5} />
          </ReactFlow>

          {/* Floating Legend Overlay */}
          <div className="absolute bottom-4 left-4 z-10 pointer-events-auto">
            <TopologyLegend isStage2={stage === 2} />
          </div>
        </div>

        {/* Side Detail Inspector Panel */}
        {selectedNode && (
          <div className="z-20 h-full">
            <NodeDetailPanel
              nodeData={selectedNodeData}
              nodeType={selectedNodeType}
              onClose={() => setSelectedNode(null)}
            />
          </div>
        )}

        {selectedEdge && (
          <div className="z-20 h-full">
            <LinkDetailPanel
              linkData={selectedEdgeData}
              sourceNodeId={selectedEdge.source}
              targetNodeId={selectedEdge.target}
              onClose={() => setSelectedEdge(null)}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export const TopologyPage: React.FC = () => {
  const [stage, setStage] = useState<number>(2);

  return (
    <ReactFlowProvider>
      <TopologyCanvas stage={stage} onSelectStage={(s) => setStage(s)} />
    </ReactFlowProvider>
  );
};

export default TopologyPage;
