# Escalation Policy: Chronic Line Fault (3+ Tickets in 30 Days)

**Document ID:** ESC-0012
**Category:** Escalation Policy
**Severity Classification:** P1 - Critical
**Applicable Service:** Airtel 5G Home Internet
**Owner:** Airtel Customer Care Leadership

## Summary
Defines the escalation path, response time targets, and approval requirements when a Tier 1 or Tier 2 agent encounters a case involving chronic line fault (3+ tickets in 30 days).

## Trigger Conditions
- Customer explicitly requests escalation or expresses intent to cancel service or pursue legal/regulatory action.
- Case matches the pattern of chronic line fault (3+ tickets in 30 days) as defined by the classification rules in the CRM case categorization module.
- SLA breach detected automatically by the monitoring system for business-class 5G Home Internet circuits.

## Escalation Path
1. **Tier 1 Agent:** Attempt standard resolution per the relevant Troubleshooting Guide or SOP. If unresolved within 30 minutes, escalate to Tier 2.
2. **Tier 2 Specialist:** Review network diagnostics and account history. If the issue involves core network infrastructure or requires engineering involvement, escalate to Tier 3 / NOC within 2 hours.
3. **Tier 3 / NOC or Cisco TAC:** Engage network engineering, open a vendor case if hardware failure is suspected (OLT, BNG, CMTS chassis), and provide the customer with a case reference number.
4. **Duty Manager:** For cases involving chronic line fault (3+ tickets in 30 days), the Duty Manager must be notified within 15 minutes of escalation trigger and must approve any compensation, credit, or executive communication.

## Response Time Targets (SLA)
- Acknowledgement to customer: 15 minutes
- Initial diagnostic update: 2 hours
- Target resolution: 4 hours

## Approval Requirements
Any service credit above $75 or equipment replacement outside standard warranty requires Duty Manager approval. Regulatory or legal-related cases must be forwarded to the Compliance team within 1 business hour, with no agent-issued commitments made to the customer regarding legal outcomes.

## Communication Guidelines
Agents must remain factual, avoid speculation about root cause until confirmed by Tier 2/3, and document every customer interaction verbatim in the case notes for potential regulatory review.

## Related Documents
- SOP: SOP-0013
- Historical Case Reference: CASE-0013

## Tags
escalation, p1 - critical, 5g home internet, airtel, sla
