import React from 'react';
import { Card } from '../components/common/Card';
import { BrainCircuit, GitMerge, FileSearch, Sparkles } from 'lucide-react';
import { Badge } from '../components/common/Badge';

export const DiagnosisPage: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="p-4 rounded-lg bg-noc-900 border border-noc-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-md bg-noc-850 text-indigo-400 border border-noc-800">
            <BrainCircuit className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-noc-100">Automated AI Network Diagnosis</h2>
            <p className="text-xs text-noc-400">
              LangGraph-powered multi-stage diagnosis with BM25 RAG corpus integration
            </p>
          </div>
        </div>
        <Badge variant="indigo" size="sm">Milestone 4 — Ready for UI Integration</Badge>
      </div>

      <Card
        title="Diagnostic Graph Pipeline"
        subtitle="Autonomous root-cause analysis workflow"
      >
        <div className="p-6 bg-noc-950/60 rounded-lg border border-noc-800 text-center max-w-2xl mx-auto space-y-4">
          <div className="flex justify-center">
            <div className="p-3 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
              <BrainCircuit className="w-8 h-8" />
            </div>
          </div>
          <h3 className="text-base font-semibold text-noc-100">LangGraph Execution Inspector</h3>
          <p className="text-xs text-noc-400 leading-relaxed">
            The backend M4 diagnostic workflow is fully validated with 280+ tests. It executes an autonomous multi-node graph:
            anomaly triage, telemetry retrieval, BM25 networking manual queries, hypothesis generation, and remediation planning.
          </p>
          <div className="pt-2 flex flex-wrap justify-center gap-2">
            <span className="px-2.5 py-1 rounded bg-noc-850 text-noc-300 border border-noc-800 text-xs font-mono">
              telemetry_node
            </span>
            <span className="text-noc-600">→</span>
            <span className="px-2.5 py-1 rounded bg-noc-850 text-noc-300 border border-noc-800 text-xs font-mono">
              rag_retriever
            </span>
            <span className="text-noc-600">→</span>
            <span className="px-2.5 py-1 rounded bg-noc-850 text-noc-300 border border-noc-800 text-xs font-mono">
              hypothesize_node
            </span>
            <span className="text-noc-600">→</span>
            <span className="px-2.5 py-1 rounded bg-noc-850 text-noc-300 border border-noc-800 text-xs font-mono">
              proposal_node
            </span>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
        <div className="p-4 rounded-lg bg-noc-900 border border-noc-800">
          <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
            <FileSearch className="w-4 h-4 text-accent-cyan" />
            BM25 Knowledge Retrieval
          </div>
          <p className="text-noc-400 leading-relaxed">
            Inspect matching RFCs, Linux tc manual snippets, and Cisco routing guidelines retrieved for the detected symptom.
          </p>
        </div>

        <div className="p-4 rounded-lg bg-noc-900 border border-noc-800">
          <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
            <GitMerge className="w-4 h-4 text-indigo-400" />
            State Graph Step Trace
          </div>
          <p className="text-noc-400 leading-relaxed">
            Visual inspection of LangGraph state transitions, confidence scores, and hypothesis evolution across iteration cycles.
          </p>
        </div>

        <div className="p-4 rounded-lg bg-noc-900 border border-noc-800">
          <div className="flex items-center gap-2 font-semibold text-noc-200 mb-1">
            <Sparkles className="w-4 h-4 text-amber-400" />
            Action Proposal Generation
          </div>
          <p className="text-noc-400 leading-relaxed">
            Structured remediation proposals with predicted impact, resource targets, and validated inverse rollback operations.
          </p>
        </div>
      </div>
    </div>
  );
};
