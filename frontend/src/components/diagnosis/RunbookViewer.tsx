import React, { useState } from 'react';
import { BookOpen, ChevronDown, ChevronUp } from 'lucide-react';
import type { RunbookRetrievalItem } from '../../api/types';

interface RunbookViewerProps {
  runbooks?: RunbookRetrievalItem[];
  isLoading?: boolean;
}

export const RunbookViewer: React.FC<RunbookViewerProps> = ({
  runbooks = [],
  isLoading = false,
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(
    runbooks.length > 0 ? runbooks[0].runbook_id : null
  );

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  return (
    <div className="p-4 bg-noc-900 border border-noc-800 rounded-lg space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs border-b border-noc-800/80 pb-2">
        <div className="flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-accent-cyan" />
          <span className="font-semibold text-noc-100">
            Retrieved Advisory Runbooks ({runbooks.length})
          </span>
        </div>
        <span className="text-[10px] font-mono text-noc-400">
          Ranked by BM25 Lexical Retrieval Score
        </span>
      </div>

      {/* Methodology Disclaimer */}
      <div className="px-3 py-1.5 bg-noc-950/60 border border-noc-800 rounded text-[11px] text-noc-400">
        <strong className="text-noc-300">RAG Invariant:</strong> BM25 scores reflect lexical keyword and feature relevance between the incident evidence and the operational runbook corpus (RB-01 through RB-10), not model confidence.
      </div>

      {isLoading ? (
        <div className="space-y-2 animate-pulse">
          <div className="h-14 bg-noc-950/60 rounded border border-noc-800" />
          <div className="h-14 bg-noc-950/60 rounded border border-noc-800" />
        </div>
      ) : runbooks.length === 0 ? (
        <p className="text-xs text-noc-500 italic py-3 text-center">
          No operational runbooks retrieved for this diagnosis.
        </p>
      ) : (
        <div className="space-y-2">
          {runbooks.map((rb) => {
            const isExpanded = expandedId === rb.runbook_id;
            return (
              <div
                key={rb.runbook_id}
                className="bg-noc-950/60 border border-noc-800 rounded-lg overflow-hidden transition-colors"
              >
                {/* Header Row */}
                <button
                  onClick={() => toggleExpand(rb.runbook_id)}
                  className="w-full p-3 flex items-center justify-between text-left text-xs hover:bg-noc-850/40 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <span className="px-2 py-0.5 rounded font-mono font-bold text-accent-cyan bg-accent-cyan/10 border border-accent-cyan/20">
                      {rb.runbook_id}
                    </span>
                    <span className="font-medium text-noc-100">{rb.title}</span>
                  </div>

                  <div className="flex items-center gap-3">
                    <span className="text-[11px] font-mono text-noc-400 bg-noc-900 px-2 py-0.5 rounded border border-noc-800">
                      BM25: {rb.score.toFixed(2)}
                    </span>
                    {isExpanded ? (
                      <ChevronUp className="w-4 h-4 text-noc-400" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-noc-400" />
                    )}
                  </div>
                </button>

                {/* Expanded Content */}
                {isExpanded && (
                  <div className="px-3.5 pb-3.5 pt-1 text-xs text-noc-300 border-t border-noc-800/60 space-y-2 font-mono text-[11px]">
                    {rb.relevant_sections && rb.relevant_sections.length > 0 && (
                      <div>
                        <span className="text-noc-400 uppercase text-[10px] block mb-1">
                          Matched Section Headers
                        </span>
                        <div className="flex flex-wrap gap-1">
                          {rb.relevant_sections.map((sec, idx) => (
                            <span
                              key={idx}
                              className="px-1.5 py-0.5 rounded bg-noc-900 text-noc-300 border border-noc-800 text-[10px]"
                            >
                              {sec}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {rb.content && (
                      <div className="mt-2 p-3 bg-noc-950 rounded border border-noc-800/80 max-h-60 overflow-y-auto whitespace-pre-wrap leading-relaxed font-sans text-xs text-noc-300">
                        {rb.content}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
