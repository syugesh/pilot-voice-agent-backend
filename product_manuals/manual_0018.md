# Product Manual: Huawei HG8245H ONT

**Document ID:** MAN-0018
**Category:** Product Manual
**Device Type:** Huawei HG8245H ONT
**Compatible Service:** Xfinity DSL
**Firmware Baseline:** v5.8.14
**Last Reviewed:** 2025-10-23

## Summary
Technical reference manual for support agents covering the Huawei HG8245H ONT, including hardware specifications, LED indicator meanings, default configuration, and supported protocols for Xfinity DSL deployments.

## Hardware Overview
The Huawei HG8245H ONT supports DSL connectivity and includes an integrated 4-port Gigabit Ethernet switch, dual-band 802.11ax WiFi radios, and an integrated VoIP ATA with two RJ-11 ports.

## LED Indicator Reference
| LED | State | Meaning |
|---|---|---|
| Power | Solid Green | Device powered and booted |
| Power | Blinking Red | Firmware corruption / boot failure |
| WAN/Internet | Solid Green | WAN link authenticated, internet reachable |
| WAN/Internet | Blinking Amber | Attempting PPPoE/DHCP authentication |
| WAN/Internet | Solid Red | Loss of signal (LOS) or WAN down |
| WiFi | Solid Blue | Wireless radios active |
| DSL Sync | Solid Green | Line synced within normal parameters |
| DSL Sync | Off | No sync - check physical cabling |

## Default Configuration
- Default gateway IP: 192.168.1.1 (or 192.168.0.1 on legacy units)
- Default admin credentials: printed on device label; must be changed on first login per security policy
- DHCP pool: 192.168.1.100 - 192.168.1.199 (100 addresses)
- WAN protocol support: PPPoE, DHCP (IPoE), Static IP, IPv6 (DHCPv6-PD, SLAAC)

## Supported Protocols and Features
- IPv4 and IPv6 dual-stack
- DNS relay and custom DNS server configuration
- Port forwarding, DMZ, and UPnP
- VPN passthrough (IPSec, PPTP, L2TP) and select models support built-in WireGuard/OpenVPN client
- QoS traffic prioritization for VoIP and gaming traffic
- WPA2-Personal and WPA3-Personal WiFi security
- TR-069 (CWMP) remote management for ISP provisioning

## Common Configuration Tasks
1. **Changing WiFi SSID/Password:** Navigate to Wireless Settings > Basic, update SSID and Security Key, click Apply, and allow 60 seconds for radios to restart.
2. **Enabling Bridge Mode:** Navigate to Advanced > WAN Settings, select Bridge Mode, and confirm; note this disables NAT/DHCP/firewall on the device.
3. **Firmware Update:** Navigate to Administration > Firmware Update; the device checks the Xfinity provisioning server automatically and can be triggered manually.
4. **Factory Reset:** Hold the recessed reset button for 10-15 seconds until all LEDs flash simultaneously.

## Related Documents
- Installation Guide: INSTALL-0019
- Firmware Release Notes: FW-0019
- Troubleshooting Guide: TS-0019

## Tags
product-manual, dsl, huawei hg8245h ont, hardware-reference, xfinity
