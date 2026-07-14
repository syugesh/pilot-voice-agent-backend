# Troubleshooting Guide: Router firmware update failure on Cable/DOCSIS Service

**Document ID:** TS-0025
**Category:** Troubleshooting
**Applies To:** Cisco TAC Cable/DOCSIS, WiFi 6 Router
**Severity:** Medium
**Last Reviewed:** 2025-12-08

## Summary
Step-by-step diagnostic procedure for resolving router firmware update failure reported by subscribers on Cisco TAC Cable/DOCSIS circuits terminating on a WiFi 6 Router.

## Problem Description
Customers report router firmware update failure. This may manifest during peak hours (7 PM - 11 PM local time) or intermittently throughout the day, and is frequently accompanied by elevated packet loss or authentication retries.

## Symptoms
- Subscriber reports router firmware update failure, sometimes with duration and frequency data.
- WiFi 6 Router status LEDs show abnormal state (blinking amber, solid red, or no PPP/online light).
- Sync rate or optical power readings outside normal operating range.
- Repeated DHCP or PPPoE renegotiation events visible in device logs.
- Ping tests to the default gateway or first-hop router show elevated latency (>150ms) or packet loss (>2%).

## Root Cause
Investigation typically traces this issue to misconfigured DNS forwarders on the CPE resolving to unreachable upstream resolvers.

## Resolution Steps
1. Pull the WiFi 6 Router event log via the local admin UI or remotely via TR-069/ACS and identify the timestamp of the last successful WAN authentication.
2. Verify line/optical statistics: for DSL check SNR margin and attenuation; for fiber check Rx power (should be between -8dBm and -28dBm); for cable check upstream/downstream SNR and power levels.
3. Confirm the account is provisioned correctly in the CRM/OSS system and that no suspend or fraud flags are present.
4. Reset the PPPoE/DHCP session from the network side (BNG or CMTS) to force a clean re-authentication.
5. If firmware is more than 2 versions behind current, schedule a firmware push per the Firmware Update SOP.
6. If physical layer readings are out of spec, dispatch a field technician to inspect the drop cable, splice enclosure, or ONT connector for damage or contamination.
7. Validate resolution by running a sustained 15-minute ping/traceroute test and a throughput test against the ISP's speed test server.

## Escalation Rules
If the issue is not resolved after two remote resets and physical layer readings remain out of spec, escalate to Tier 2 Network Operations within 4 business hours. If the customer is a business-class subscriber with an SLA, escalate immediately to Tier 3 / Cisco TAC per the Business SLA Escalation Policy.

## Related Documents
- FAQ: FAQ-0026
- Error Code Reference: ERR-0026
- Escalation Policy: ESC-0026

## Tags
troubleshooting, cable/docsis, wifi 6 router, cisco tac, network-diagnostics
