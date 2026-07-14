# Escalation Policy: Billing Dispute Above Threshold

**Document ID:** ESC-0003
**Category:** Escalation Policy
**Severity Classification:** P4 - Low
**Applicable Service:** AT&T Fiber
**Owner:** AT&T Customer Care Leadership

## Summary
Defines the escalation path, response time targets, and approval requirements when a Tier 1 or Tier 2 agent encounters a case involving billing dispute above threshold.

## Trigger Conditions
- Customer explicitly requests escalation or expresses intent to cancel service or pursue legal/regulatory action.
- Case matches the pattern of billing dispute above threshold as defined by the classification rules in the CRM case categorization module.
- SLA breach detected automatically by the monitoring system for business-class Fiber circuits.

## Escalation Path
1. **Tier 1 Agent:** Attempt standard resolution per the relevant Troubleshooting Guide or SOP. If unresolved within 60 minutes, escalate to Tier 2.
2. **Tier 2 Specialist:** Review network diagnostics and account history. If the issue involves core network infrastructure or requires engineering involvement, escalate to Tier 3 / NOC within 2 hours.
3. **Tier 3 / NOC or Cisco TAC:** Engage network engineering, open a vendor case if hardware failure is suspected (OLT, BNG, CMTS chassis), and provide the customer with a case reference number.
4. **Duty Manager:** For cases involving billing dispute above threshold, the Duty Manager must be notified within 15 minutes of escalation trigger and must approve any compensation, credit, or executive communication.

## Response Time Targets (SLA)
- Acknowledgement to customer: 1 hour
- Initial diagnostic update: 2 hours
- Target resolution: 24 hours

## Approval Requirements
Any service credit above $100 or equipment replacement outside standard warranty requires Duty Manager approval. Regulatory or legal-related cases must be forwarded to the Compliance team within 1 business hour, with no agent-issued commitments made to the customer regarding legal outcomes.

## Communication Guidelines
Agents must remain factual, avoid speculation about root cause until confirmed by Tier 2/3, and document every customer interaction verbatim in the case notes for potential regulatory review.

## Related Documents
- SOP: SOP-0004
- Historical Case Reference: CASE-0004

## Tags
escalation, p4 - low, fiber, at&t, sla
