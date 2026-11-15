"""LangGraph StateGraph builder for NETfix Diagnostic Agent."""

from typing import Any, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from netfix.agent.nodes import (
    approval_gate_node,
    diagnose_node,
    investigate_node,
    propose_action_node,
    remediate_node,
    retrieve_runbooks_node,
    rollback_node,
    route_after_approval,
    route_after_remediation,
    route_after_validation,
    route_after_verification,
    triage_node,
    validate_node,
    verify_node,
)
from netfix.agent.state import DiagnosticState


def route_after_triage(state: DiagnosticState) -> str:
    """Route to investigate if actionable, otherwise terminate at END."""
    if state.get("is_actionable", False):
        return "investigate"
    return END


def build_diagnostic_graph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
    enable_closed_loop: bool = False,
    **closed_loop_kwargs: Any,
):
    """Build and compile the LangGraph StateGraph for network diagnosis."""
    if enable_closed_loop:
        return build_closed_loop_graph(checkpointer=checkpointer, **closed_loop_kwargs)

    workflow = StateGraph(DiagnosticState)

    # Register nodes
    workflow.add_node("triage", triage_node)
    workflow.add_node("investigate", investigate_node)
    workflow.add_node("retrieve_runbooks", retrieve_runbooks_node)
    workflow.add_node("diagnose", diagnose_node)
    workflow.add_node("propose_action", propose_action_node)
    workflow.add_node("approval_gate", approval_gate_node)

    # Register edges
    workflow.add_edge(START, "triage")
    workflow.add_conditional_edges(
        "triage",
        route_after_triage,
        {
            "investigate": "investigate",
            END: END,
        },
    )
    workflow.add_edge("investigate", "retrieve_runbooks")
    workflow.add_edge("retrieve_runbooks", "diagnose")
    workflow.add_edge("diagnose", "propose_action")
    workflow.add_edge("propose_action", "approval_gate")
    workflow.add_edge("approval_gate", END)

    saver = checkpointer if checkpointer is not None else MemorySaver()
    return workflow.compile(checkpointer=saver)


def build_closed_loop_graph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
    validator: Optional[Any] = None,
    controller: Optional[Any] = None,
    verifier: Optional[Any] = None,
    rollback_engine: Optional[Any] = None,
):
    """Build and compile the complete closed-loop LangGraph pipeline (M4 -> HITL -> M5.2 -> M5.3 -> M5.5 -> M5.4)."""
    workflow = StateGraph(DiagnosticState)

    # Register diagnosis & approval nodes
    workflow.add_node("triage", triage_node)
    workflow.add_node("investigate", investigate_node)
    workflow.add_node("retrieve_runbooks", retrieve_runbooks_node)
    workflow.add_node("diagnose", diagnose_node)
    workflow.add_node("propose_action", propose_action_node)
    workflow.add_node("approval_gate", approval_gate_node)

    # Register M5 closed-loop remediation & verification nodes
    workflow.add_node("validate", lambda s: validate_node(s, validator=validator))
    workflow.add_node("remediate", lambda s: remediate_node(s, controller=controller))
    workflow.add_node("verify", lambda s: verify_node(s, verifier=verifier))
    workflow.add_node("rollback", lambda s: rollback_node(s, rollback_engine=rollback_engine))

    # Edges
    workflow.add_edge(START, "triage")
    workflow.add_conditional_edges(
        "triage",
        route_after_triage,
        {
            "investigate": "investigate",
            END: END,
        },
    )
    workflow.add_edge("investigate", "retrieve_runbooks")
    workflow.add_edge("retrieve_runbooks", "diagnose")
    workflow.add_edge("diagnose", "propose_action")
    workflow.add_edge("propose_action", "approval_gate")

    # M5 Closed-loop pipeline edges
    workflow.add_conditional_edges(
        "approval_gate",
        route_after_approval,
        {
            "validate": "validate",
            END: END,
        },
    )
    workflow.add_conditional_edges(
        "validate",
        route_after_validation,
        {
            "remediate": "remediate",
            END: END,
        },
    )
    workflow.add_conditional_edges(
        "remediate",
        route_after_remediation,
        {
            "verify": "verify",
            END: END,
        },
    )
    workflow.add_conditional_edges(
        "verify",
        route_after_verification,
        {
            "rollback": "rollback",
            END: END,
        },
    )
    workflow.add_edge("rollback", END)

    saver = checkpointer if checkpointer is not None else MemorySaver()
    return workflow.compile(checkpointer=saver)
