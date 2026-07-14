# Network Reference: DHCP Lease Lifecycle on Residential Gateways

**Document ID:** NET-0002
**Category:** Network Architecture Reference
**Related Technology:** Fixed Wireless
**Audience:** Tier 2/Tier 3 Support, Network Engineering
**Last Reviewed:** 2025-06-12

## Summary
Technical reference document explaining dhcp lease lifecycle on residential gateways for support staff who need to understand the underlying network architecture behind Cisco TAC Fixed Wireless services when diagnosing complex customer issues.

## Overview
DHCP Lease Lifecycle on Residential Gateways is a foundational concept in modern Fixed Wireless network design. Understanding this architecture allows Tier 2 and Tier 3 agents to more accurately isolate whether a reported issue originates at the customer premises, the access network, or the ISP core.

## Technical Details
In a typical Cisco TAC deployment, subscriber traffic originates at the customer's CPE (Fixed Wireless modem/router/ONT) and traverses the access network before reaching the aggregation layer. Key elements relevant to dhcp lease lifecycle on residential gateways include:

- **Authentication Layer:** Most residential connections authenticate via PPPoE (PPP over Ethernet) against a BNG, or via DHCP-based IPoE for simpler IP assignment, depending on the Cisco TAC network design.
- **Address Assignment:** IPv4 addresses may be assigned via DHCP from a CGNAT pool for residential subscribers, while IPv6 is typically assigned via SLAAC or DHCPv6 prefix delegation (DHCPv6-PD) to give the customer a routable /56 or /60 prefix.
- **DNS Path:** Subscriber DNS queries are typically forwarded through the CPE's DNS relay to ISP-operated recursive resolvers, unless the customer has manually configured third-party DNS servers (e.g., 1.1.1.1, 8.8.8.8).
- **Physical/Access Layer:** Fixed Wireless networks vary in their access layer design — GPON fiber networks use passive optical splitters between the OLT and multiple ONTs, while DOCSIS cable networks share bandwidth across a node via the CMTS, and DSL networks terminate individually at a DSLAM.

## Relevance to Customer Support
When a customer reports symptoms such as intermittent drops, slow speeds, or authentication failures, understanding dhcp lease lifecycle on residential gateways helps the agent determine:
1. Whether the issue is isolated to a single customer (pointing to CPE or drop-cable level issues) or affects multiple customers on the same access node (pointing to shared infrastructure issues like an oversubscribed CMTS node or OLT PON port).
2. Whether authentication-layer troubleshooting (PPPoE/DHCP resets) or physical-layer troubleshooting (signal levels, cabling) is the appropriate next step.
3. Whether the customer's own network configuration (e.g., a secondary router causing double-NAT) could be contributing to the reported symptom.

## Diagnostic Commands and Data Points
- `ping` and `traceroute` from the CPE to identify where latency or loss is introduced.
- Optical power readings (Rx/Tx) for fiber, SNR/attenuation for DSL, and upstream/downstream power levels for DOCSIS.
- PPPoE session logs on the BNG showing authentication timestamps and disconnect reasons.
- DHCP server logs showing lease assignment, renewal, and any scope exhaustion events.

## Related Documents
- Troubleshooting Guide: TS-0003
- Error Code Reference: ERR-0003
- Product Manual: MAN-0003

## Tags
network-architecture, fixed wireless, cisco tac, tier2-reference, diagnostics
